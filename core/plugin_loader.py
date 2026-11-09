"""Safe importer for file-backed plugins with deterministic hot reload."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterable


def _normalize_search_dirs(search_dir: str | Path | Iterable[str | Path] | None) -> list[Path]:
    if search_dir is None:
        return []
    values = [search_dir] if isinstance(search_dir, (str, Path)) else list(search_dir)
    out: list[Path] = []
    for value in values:
        path = Path(value).resolve()
        if path not in out:
            out.append(path)
    return out


def _find_local_module(module_name: str, roots: list[Path]) -> tuple[Path, bool] | None:
    relative = Path(*module_name.split("."))
    for root in roots:
        module_file = (root / relative).with_suffix(".py")
        if module_file.is_file():
            return module_file, False
        package_file = root / relative / "__init__.py"
        if package_file.is_file():
            return package_file, True
    return None


def _is_under(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.resolve().relative_to(root)
            return True
        except (OSError, ValueError):
            pass
    return False


def _evict_local_module(module_name: str, roots: list[Path]) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        return
    module_file = getattr(module, "__file__", None)
    if module_file and _is_under(Path(module_file), roots):
        cached = getattr(module, "__cached__", None)
        if cached:
            try:
                Path(cached).unlink(missing_ok=True)
            except OSError:
                pass
        sys.modules.pop(module_name, None)
        return
    if _find_local_module(module_name, roots) is not None:
        raise ImportError(
            f"plugin module name collision: {module_name!r} is already loaded from {module_file or 'unknown source'}"
        )


def _load_source_module(module_name: str, source: Path, is_package: bool):
    """Execute plugin source directly, bypassing timestamp-based bytecode caches."""
    locations = [str(source.parent)] if is_package else None
    spec = importlib.util.spec_from_file_location(
        module_name,
        source,
        submodule_search_locations=locations,
    )
    if spec is None:
        raise ImportError(f"cannot create import spec for {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        code = compile(source.read_bytes(), str(source), "exec")
        exec(code, module.__dict__)  # pylint: disable=exec-used
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def import_plugin_class(
    class_path: str,
    search_dir: str | Path | Iterable[str | Path] | None = None,
    *,
    force_reload: bool = False,
) -> type[Any]:
    """Import ``module.Class`` without permanently mutating ``sys.path``.

    Local plugin source is loaded directly when present under a supplied search
    root.  This also lets a runtime rooted outside the main project contribute a
    submodule such as ``detectors.my_detector`` even when the host ``detectors``
    package is already imported.  ``force_reload`` always executes current source
    bytes, so fast same-second edits cannot be hidden by ``.pyc`` timestamps.
    """
    if not isinstance(class_path, str) or "." not in class_path:
        raise ValueError("class_path must be '<module>.<ClassName>'")
    module_name, class_name = class_path.rsplit(".", 1)
    if not module_name or not class_name:
        raise ValueError("class_path must be '<module>.<ClassName>'")

    roots = _normalize_search_dirs(search_dir)
    added: list[str] = []
    for root in reversed(roots):
        value = str(root)
        if value not in sys.path:
            sys.path.insert(0, value)
            added.append(value)

    try:
        importlib.invalidate_caches()
        local = _find_local_module(module_name, roots)
        existing = sys.modules.get(module_name)
        if force_reload:
            _evict_local_module(module_name, roots)
            existing = None
        if existing is not None:
            module = existing
        elif local is not None:
            module = _load_source_module(module_name, *local)
        else:
            module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
    finally:
        for value in added:
            try:
                sys.path.remove(value)
            except ValueError:
                pass

    if not isinstance(cls, type):
        raise TypeError(f"{class_path} is not a class")
    return cls
