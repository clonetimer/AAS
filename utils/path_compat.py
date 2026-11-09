"""Path containment across supported Python versions."""
from pathlib import Path


def is_relative_to(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
    except ValueError:
        return False
    return True
