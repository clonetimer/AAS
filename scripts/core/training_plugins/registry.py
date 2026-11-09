"""训练策略注册表、按名称解析与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class

from .base import BaseTrainingStrategy


TRAINING_STRATEGY_REGISTRY: dict[str, BaseTrainingStrategy] = {}


def register_strategy(strategy: BaseTrainingStrategy) -> None:
    if not isinstance(strategy, BaseTrainingStrategy):
        raise TypeError("Training strategy plugin must inherit BaseTrainingStrategy.")
    if not strategy.name:
        raise ValueError("TrainingStrategy must define a non-empty `name`.")
    TRAINING_STRATEGY_REGISTRY[strategy.name] = strategy


def get_strategy(name: str) -> Optional[BaseTrainingStrategy]:
    return TRAINING_STRATEGY_REGISTRY.get(name)


def available_strategies() -> list[dict]:
    return [
        {
            "name": name,
            "label": s.label,
            "description": s.description,
            "dependencies": s.dependencies,
        }
        for name, s in TRAINING_STRATEGY_REGISTRY.items()
    ]


def discover_strategies(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义训练策略。

    YAML 形如::

        name: cv
        label: 交叉验证
        description: K 折交叉验证训练
        class_path: my_package.my_module.CvStrategy
        dependencies: []

    返回成功注册的策略数量。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent.parent / "configs" / "training_strategies"
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
            strategy = cls()
            if data.get("name"):
                strategy.name = data["name"]
            if data.get("label"):
                strategy.label = data["label"]
            if data.get("description"):
                strategy.description = data["description"]
            if data.get("dependencies"):
                strategy.dependencies = data["dependencies"]
            register_strategy(strategy)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
