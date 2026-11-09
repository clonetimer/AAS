#!/usr/bin/env python3
"""Deterministic per-file Pylint gate for Fatal, Error, and Warning findings.

Examples:
    python scripts/dev/pylint_gate.py
    python scripts/dev/pylint_gate.py detectors/statistical_detectors.py
    python scripts/dev/pylint_gate.py --jobs 4 --report lint-report.json

Warnings intentionally suppressed next to documented test hooks remain visible
in source control; new unsuppressed warnings fail this gate.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def _source_files(requested: list[str]) -> list[Path]:
    """Select source files, rejecting paths outside the managed project."""
    if not requested:
        return sorted(
            path for path in PROJECT_ROOT.rglob("*.py")
            if not SKIP_DIRS.intersection(path.relative_to(PROJECT_ROOT).parts)
        )
    selected = []
    for raw_path in requested:
        path = (PROJECT_ROOT / raw_path).resolve()
        if not is_relative_to(path, PROJECT_ROOT) or not path.is_file() or path.suffix != ".py":
            raise ValueError(f"Expected a Python source file within project: {raw_path}")
        selected.append(path)
    return sorted(set(selected))


def _check_one(path: Path, timeout: int) -> dict:
    """Lint a single module to isolate stale import caches and bound execution."""
    cmd = [sys.executable, "-m", "pylint", "--persistent=n", "--reports=n",
           "--disable=all", "--enable=E,F,W", "--output-format=json", str(path)]
    name = path.relative_to(PROJECT_ROOT).as_posix()
    try:
        # Isolated Pylint processes must resolve project imports identically
        # to pytest, regardless of the calling shell's PYTHONPATH.
        env = os.environ.copy()
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + current if current else "")
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=timeout, check=False, env=env)
        payload = json.loads(result.stdout or "[]")
        if not isinstance(payload, list):
            raise ValueError("Pylint produced non-list JSON output")
        return {"file": name, "diagnostics": payload, "returncode": result.returncode,
                "stderr": result.stderr, "failed": bool(result.stderr.strip())}
    except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        return {"file": name, "diagnostics": [], "returncode": -1,
                "stderr": str(exc), "failed": True}


def main() -> int:
    """Run bounded parallel checks and fail closed on Pylint execution errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional project-relative .py paths")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--report", type=Path, help="Optional JSON results path")
    args = parser.parse_args()
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be positive")
    try:
        sources = _source_files(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        reports = list(executor.map(lambda path: _check_one(path, args.timeout), sources))
    findings = [(item["file"], msg) for item in reports for msg in item["diagnostics"]]
    failed = [item for item in reports if item["failed"]]
    for path, diag in findings:
        print(f"{path}:{diag.get('line', 0)}: {diag.get('symbol')}: {diag.get('message')}")
    for item in failed:
        print(f"Pylint execution failed: {item['file']}: {item['stderr']}", file=sys.stderr)
    result = {"scanned_files": len(reports), "findings": len(findings),
              "execution_failures": len(failed), "results": reports}
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Pylint E/F/W gate: {len(reports)} files; {len(findings)} diagnostics; "
          f"{len(failed)} execution failures")
    return 1 if findings or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
