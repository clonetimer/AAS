from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator.orchestrator import EvaluationOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Anomaly detection evaluation system.")
    parser.add_argument("--config", required=True, help="Path to experiment config file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    orchestrator = EvaluationOrchestrator()
    summary = orchestrator.run_experiment(args.config)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    summary_path = Path(summary["run_dir"]) / "reports" / "summary.json"
    print(f"Summary written to: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
