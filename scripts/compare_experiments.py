"""Compare two archived AAS runs using verified detector-input digests.

Usage from the project root:
    python scripts/compare_experiments.py <experiment_id_A> <experiment_id_B>

No model fitting is performed. This is a read-only evidence inspection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.server.experiment_comparison import compare_archived_experiments
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.server.experiment_comparison import compare_archived_experiments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('first_experiment_id')
    parser.add_argument('second_experiment_id')
    args = parser.parse_args()
    result = compare_archived_experiments(Path(__file__).resolve().parents[1],
                                          args.first_experiment_id,
                                          args.second_experiment_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
