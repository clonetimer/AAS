"""预处理算子注册表、默认管线与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class

from .base import BasePreprocessOperator


PREPROCESS_REGISTRY: dict[str, BasePreprocessOperator] = {}

# 默认管线顺序：填充缺失 → 修复异常值 → 平滑 → 归一化（与原 PreprocessingManager 一致）
DEFAULT_PIPELINE = ["fill_missing", "repair_outlier", "smooth", "normalize"]


def register_operator(operator: BasePreprocessOperator) -> None:
    if not isinstance(operator, BasePreprocessOperator):
        raise TypeError("Preprocess plugin must inherit BasePreprocessOperator.")
    if not operator.name:
        raise ValueError("Preprocess operator must define a non-empty `name`.")
    PREPROCESS_REGISTRY[operator.name] = operator


def get_operator(name: str) -> Optional[BasePreprocessOperator]:
    return PREPROCESS_REGISTRY.get(name)


def available_operators() -> list[dict]:
    return [
        {
            "name": name,
            "label": op.label,
            "description": op.description,
            "dependencies": op.dependencies,
            "order": getattr(op, "order", None),
            "default_enabled": getattr(op, "default_enabled", True),
            "config_schema": getattr(op, "config_schema", []),
        }
        for name, op in PREPROCESS_REGISTRY.items()
    ]


def resolve_pipeline(step_names: Optional[list[str]] = None) -> list[str]:
    """Resolve an explicit pipeline or the complete registered default pipeline.

    With no explicit steps, every registered contribution participates and is
    ordered by its ``order`` metadata.  Registration order is the stable
    tiebreaker.  Explicit pipelines retain the caller's order and silently skip
    unknown names for backwards compatibility.
    """
    if step_names is not None:
        return [name for name in step_names if name in PREPROCESS_REGISTRY]

    indexed = list(enumerate(PREPROCESS_REGISTRY.items()))
    indexed.sort(key=lambda item: (getattr(item[1][1], "order", 1000), item[0]))
    return [name for _index, (name, _op) in indexed]


def run_pipeline(
    step_names: Optional[list[str]],
    rows: list[dict],
    config: dict,
) -> list[dict]:
    """Execute the resolved pipeline, skipping disabled or unknown operators."""
    out = rows
    for name in resolve_pipeline(step_names):
        op = get_operator(name)
        if op is None or not op.enabled(config):
            continue
        out = op.apply(out, config)
    return out


def discover_preprocessors(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义预处理算子。

    YAML 形如::

        name: shift
        label: Shift
        description: 平移数值
        class_path: my_package.my_module.ShiftOperator
        dependencies: []

    返回成功注册的算子数量。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent.parent / "configs" / "preprocessors"
    if not search_dir.exists():
        return 0
    count = 0
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict) or "class_path" not in data:
            continue
        try:
            cls = import_plugin_class(data["class_path"], search_dir)
            op = cls()
            if data.get("name"):
                op.name = data["name"]
            if data.get("label"):
                op.label = data["label"]
            if data.get("description"):
                op.description = data["description"]
            if data.get("dependencies"):
                op.dependencies = data["dependencies"]
            if data.get("order") is not None:
                op.order = int(data["order"])
            if data.get("default_enabled") is not None:
                op.default_enabled = bool(data["default_enabled"])
            if isinstance(data.get("config_schema"), list):
                op.config_schema = list(data["config_schema"])
            register_operator(op)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
