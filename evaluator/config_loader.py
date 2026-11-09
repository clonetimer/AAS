from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        return json.loads(text)

    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load YAML config files.") from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Config root must be a mapping.")
        return data

    raise ValueError(f"Unsupported config format: {suffix}")


def find_project_root(start: Path) -> Path:
    """Locate the project root by walking up until a `configs/` directory is found.

    Relative paths inside experiment configs (e.g. `configs/detectors/x.yaml`,
    `datasets/x.db`) are written relative to the project root, not to the
    experiment file itself. Resolving them against the CWD breaks whenever the
    tool is launched from another directory, so anchor on the project root.
    """
    for candidate in (start, *start.parents):
        if (candidate / "configs").is_dir():
            return candidate
    return Path.cwd()


def resolve_detector_spec(spec: dict[str, Any], root: Path) -> dict[str, Any]:
    """Expand a detector spec by inlining the external config it points to.

    An experiment config may declare a detector either inline:

        - name: isolation_forest_detector
          config: {n_estimators: 200}

    or by reference to a detector config file:

        - name: isolation_forest_detector
          config_path: configs/detectors/isolation_forest_detector.yaml

    Reference files have the shape `{name, class_path, config}`. Without this
    expansion the runner only ever sees `config_path` and instantiates detectors
    with an empty config, silently discarding every tuned parameter.

    Precedence (highest first): inline `config` > `config_path` file `config`.
    """
    resolved = dict(spec)
    config = dict(spec.get("config") or {})

    config_path = spec.get("config_path")
    if config_path:
        path = Path(config_path)
        if not path.is_absolute():
            path = root / config_path
        if not path.exists():
            raise FileNotFoundError(
                f"Detector config file not found: {config_path} (resolved to {path})"
            )
        external = load_config(str(path))
        if not isinstance(external, dict):
            raise ValueError(f"Detector config root must be a mapping: {config_path}")
        config = {**(external.get("config") or {}), **config}
        if not resolved.get("class_path") and external.get("class_path"):
            resolved["class_path"] = external["class_path"]

    resolved["config"] = config
    return resolved


def resolve_detector_specs(
    detectors: list[dict[str, Any]], root: Path
) -> list[dict[str, Any]]:
    return [resolve_detector_spec(spec, root) for spec in detectors]
