from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class ExperimentManager:
    def create_experiment(self, config_path: str, output_dir: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_id = f"exp_{timestamp}"
        run_dir = Path(output_dir) / experiment_id
        for relative in ("logs", "raw", "reports", "tasks"):
            (run_dir / relative).mkdir(parents=True, exist_ok=True)
        metadata = {
            "experiment_id": experiment_id,
            "config_path": str(Path(config_path).resolve()),
            "created_at": datetime.now().isoformat(),
        }
        self.save_metadata(experiment_id, output_dir, metadata)
        return experiment_id

    def get_run_dir(self, experiment_id: str, output_dir: str) -> str:
        return str((Path(output_dir) / experiment_id).resolve())

    def save_metadata(self, experiment_id: str, output_dir: str, metadata: dict[str, Any]) -> None:
        run_dir = Path(output_dir) / experiment_id
        meta_file = run_dir / "metadata.json"
        meta_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
