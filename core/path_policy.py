"""Host-controlled path boundary helpers.

Feature code and plugins should work with logical project references rather than
being allowed to resolve arbitrary filesystem paths.  This module keeps path
containment, suffix checks and existence checks consistent across services.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


class ManagedPathError(ValueError):
    """Raised when a requested path crosses a host-managed boundary."""


def resolve_managed_file(
    root: str | Path,
    requested: str | Path,
    *,
    anchor: str | Path | None = None,
    allowed_suffixes: Iterable[str] | None = None,
    required_name: str | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve ``requested`` while guaranteeing that it stays below ``root``.

    ``anchor`` controls how relative logical references are interpreted.  For
    example, a browser may submit ``datasets/history.db`` while ``root`` is the
    project ``datasets`` directory and ``anchor`` is the project root.
    Symlinks are resolved before containment is checked, so they cannot be used
    to escape the managed directory.
    """

    managed_root = Path(root).resolve()
    logical_anchor = Path(anchor).resolve() if anchor is not None else managed_root
    raw_text = str(requested or "").strip()
    if not raw_text:
        raise ManagedPathError("managed path is required")

    raw = Path(raw_text)
    candidate = (raw if raw.is_absolute() else logical_anchor / raw).resolve()
    try:
        candidate.relative_to(managed_root)
    except ValueError as exc:
        raise ManagedPathError(
            f"path must stay under managed root: {managed_root.name}"
        ) from exc

    if required_name is not None and candidate.name != required_name:
        raise ManagedPathError(f"managed file must be named {required_name}")

    if allowed_suffixes is not None:
        suffixes = {
            (str(suffix).lower() if str(suffix).startswith(".") else f".{str(suffix).lower()}")
            for suffix in allowed_suffixes
        }
        if candidate.suffix.lower() not in suffixes:
            allowed = ", ".join(sorted(suffixes))
            raise ManagedPathError(f"managed file must use one of: {allowed}")

    if must_exist and not candidate.is_file():
        raise FileNotFoundError(f"managed file does not exist: {raw_text}")

    return candidate
