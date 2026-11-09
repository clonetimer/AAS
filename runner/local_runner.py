from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from evaluator.dataset_manager import DatasetManager
from runner.result_schema import failed_result, validate_result_schema
from runner.runner_plugins.base import BaseRunner


class LocalRunner(BaseRunner):
    name = "local"
    label = "本地执行器"
    description = "通过子进程在本地执行检测器"

    def run(self, task_spec: dict) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(task_spec, handle, indent=2, ensure_ascii=False)
            task_file = handle.name

        result_file = task_spec["output"]["result_file"]
        Path(result_file).parent.mkdir(parents=True, exist_ok=True)

        try:
            cmd = self.build_subprocess_cmd(task_file)
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                exec_result = self.wait_and_collect(process, int(task_spec["runtime"]["timeout_sec"]))

            log_file = Path(task_spec["output"]["log_file"])
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(
                (exec_result.get("stdout") or "") + "\n" + (exec_result.get("stderr") or ""),
                encoding="utf-8",
            )

            if exec_result["timeout"]:
                return self.build_failed_result("TIMEOUT", "runner timeout")

            if not os.path.exists(result_file):
                return self.build_failed_result("OUTPUT_SCHEMA_ERROR", "result file missing")

            return self.parse_result(result_file, task_spec=task_spec)
        except ValueError as exc:
            return self.build_failed_result("OUTPUT_SCHEMA_ERROR", str(exc))
        finally:
            try:
                os.remove(task_file)
            except OSError:
                pass

    def build_subprocess_cmd(self, task_file: str) -> list[str]:
        return [sys.executable, "-m", "runner.runner_entry", "--task", task_file]

    def wait_and_collect(self, process, timeout_sec: int) -> dict:
        try:
            stdout, stderr = process.communicate(timeout=timeout_sec)
            return {"timeout": False, "returncode": process.returncode, "stdout": stdout, "stderr": stderr}
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return {"timeout": True, "returncode": None, "stdout": stdout, "stderr": stderr}

    def parse_result(self, result_file: str, task_spec: dict | None = None) -> dict:
        result = json.loads(Path(result_file).read_text(encoding="utf-8"))
        expected_length = None
        if task_spec is not None:
            dataset_file = task_spec["dataset"]["dataset_file"]
            dataset_payload = json.loads(Path(dataset_file).read_text(encoding="utf-8"))
            if "test_X" in dataset_payload:
                expected_length = len(dataset_payload["test_X"])
            else:
                meta = dataset_payload.get("meta")
                if not isinstance(meta, dict):
                    raise ValueError("dataset snapshot missing test_X and meta")
                bundle = DatasetManager().load_dataset_from_meta(meta)
                expected_length = len(bundle.test_X)
        validate_result_schema(result, expected_length=expected_length)
        return result

    def build_failed_result(self, error_code: str, message: str) -> dict:
        return failed_result(error_code, message)
