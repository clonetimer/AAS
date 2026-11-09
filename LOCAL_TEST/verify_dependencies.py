r"""Windows-safe dependency import check (no PowerShell python -c quoting).

Run from the AAS root with:
    .\.venv\Scripts\python.exe .\LOCAL_TEST\verify_dependencies.py
"""
from __future__ import annotations

import importlib
import sys
import traceback

REQUIRED = ("numpy", "pandas", "sklearn", "torch", "yaml", "pytest")


def main() -> int:
    print("Python:", sys.executable, flush=True)
    failures = []
    for name in REQUIRED:
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            failures.append(name)
            print("FAIL:", name, type(exc).__name__, str(exc), file=sys.stderr, flush=True)
            traceback.print_exc()
        else:
            version = getattr(module, "__version__", "unknown")
            print("PASS:", name, version, flush=True)
    if failures:
        print("Core dependencies import failed:", ", ".join(failures), file=sys.stderr)
        return 1
    print("Dependencies verified.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
