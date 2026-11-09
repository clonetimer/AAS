from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.container import AppContainer, get_container
from runner.runner_plugins.registry import get_runner
from evaluator.config_loader import find_project_root, load_config, resolve_detector_specs
from evaluator.dataset_manager import DatasetBundle
from evaluator.utils.config_validator import validate_config

logger = logging.getLogger(__name__)


class EvaluationOrchestrator:
    def __init__(self, container: AppContainer | None = None) -> None:
        self.container = container or get_container()
        self.dataset_manager = self.container.dataset_manager
        self.metric_engine = self.container.cli_metric_engine
        self.report_generator = self.container.report_generator
        self.experiment_manager = self.container.experiment_manager
        self.runner = self.container.local_runner

    @staticmethod
    def _validate_config_warn_only(config: dict, config_path: str) -> None:
        """Validate config but never abort the run.

        validate_config is intentionally not a hard gate yet: it surfaces schema
        drift early via a warning without breaking existing experiments.
        """
        try:
            validate_config(config)
        except Exception as exc:  # noqa: BLE001 - warn-only by design  # pylint: disable=broad-exception-caught
            logger.warning("config validation failed (%s): %s", config_path, exc)

    def run_experiment(self, config_path: str, runtime_overrides: dict | None = None) -> dict:
        config = load_config(config_path)
        if runtime_overrides:
            runtime = dict(config.get("runtime") or {})
            runtime.update(runtime_overrides)
            config["runtime"] = runtime
        self._validate_config_warn_only(config, config_path)
        output_dir = config.get("experiment", {}).get("output_dir", "outputs")
        experiment_id = self.experiment_manager.create_experiment(config_path, output_dir)
        run_dir = self.experiment_manager.get_run_dir(experiment_id, output_dir)
        dataset_bundle = self.dataset_manager.load_dataset(config["dataset"])

        project_root = find_project_root(Path(config_path).resolve().parent)
        detector_specs = resolve_detector_specs(config["detectors"], project_root)

        detector_results = []
        for index, detector_spec in enumerate(detector_specs, start=1):
            raw_result = self.run_single_detector(
                detector_spec=detector_spec,
                dataset_bundle=dataset_bundle,
                experiment_id=experiment_id,
                run_dir=run_dir,
                runtime_config=config.get("runtime", {}),
                run_index=index,
            )
            if raw_result["status"] == "success":
                metrics = self.metric_engine.compute_all(
                    y_true=dataset_bundle.test_y,
                    anomaly_score=raw_result["output"].get("anomaly_score"),
                    pred_label=raw_result["output"].get("pred_label"),
                    config=config.get("metrics", {}),
                )
            else:
                metrics = {}

            detector_results.append(
                {
                    "detector": detector_spec["name"],
                    "raw_result": raw_result,
                    "metrics": metrics,
                }
            )

        dataset_config = dict(config.get("dataset") or {})
        experiment_config = dict(config.get("experiment") or {})
        return self.finalize_report(
            experiment_context={
                "experiment_id": experiment_id,
                "run_dir": run_dir,
                "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "type": experiment_config.get("type", "evaluation"),
                "dataset": dataset_config.get("file"),
                "table": dataset_config.get("table"),
                "config_path": str(Path(config_path).resolve()),
            },
            detector_results=detector_results,
        )

    def run_single_detector(
        self,
        detector_spec: dict,
        dataset_bundle: DatasetBundle,
        experiment_id: str,
        run_dir: str,
        runtime_config: dict,
        run_index: int,
    ) -> dict:
        run_id = f"run_{run_index:03d}_{detector_spec['name']}"
        task_dir = Path(run_dir) / "tasks"
        raw_dir = Path(run_dir) / "raw"
        logs_dir = Path(run_dir) / "logs"

        dataset_payload = {
            "meta": dataset_bundle.meta,
        }

        dataset_file = task_dir / f"{run_id}_dataset.json"
        dataset_file.write_text(json.dumps(dataset_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        task_spec = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "detector": detector_spec,
            "dataset": {
                "dataset_file": str(dataset_file),
                "feature_cols": dataset_bundle.feature_names,
            },
            "runtime": {
                "timeout_sec": int(runtime_config.get("timeout_sec", runtime_config.get("timeout", 600))),
                "save_model": bool(runtime_config.get("save_model", False)),
                "predict_label": bool(runtime_config.get("predict_label", True)),
            },
            "output": {
                "result_file": str(raw_dir / f"{run_id}_result.json"),
                "log_file": str(logs_dir / f"{run_id}.log"),
            },
        }
        runner_name = runtime_config.get("runner", "local")
        runner = get_runner(runner_name) or self.runner
        return runner.run(task_spec)

    def finalize_report(self, experiment_context: dict, detector_results: list[dict]) -> dict:
        reports_dir = Path(experiment_context["run_dir"]) / "reports"
        self.report_generator.generate_summary_json(
            experiment_context,
            detector_results,
            str(reports_dir / "summary.json"),
        )
        self.report_generator.generate_csv(
            detector_results,
            str(reports_dir / "summary.csv"),
        )
        self.report_generator.generate_markdown_report(
            experiment_context,
            detector_results,
            str(reports_dir / "report.md"),
        )
        return {
            "experiment_id": experiment_context["experiment_id"],
            "run_dir": experiment_context["run_dir"],
            "detectors": [item["detector"] for item in detector_results],
            "result_count": len(detector_results),
        }
