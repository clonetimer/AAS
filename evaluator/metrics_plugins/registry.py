"""指标注册表、自动计算与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class
from core.native_plugins import is_native_descriptor
from .native_metric import NativeMetric

from .base import BaseMetric, MetricContext


METRIC_REGISTRY: dict[str, BaseMetric] = {}

# 旧键名兼容（早期 config 使用 enable_auc_roc / enable_auc_pr）
_LEGACY_KEYS = {
    "roc_auc": "enable_auc_roc",
    "pr_auc": "enable_auc_pr",
}


def register_metric(metric: BaseMetric) -> None:
    if not isinstance(metric, BaseMetric):
        raise TypeError("Metric plugin must inherit BaseMetric.")
    if not metric.name:
        raise ValueError("Metric must define a non-empty `name`.")
    METRIC_REGISTRY[metric.name] = metric


def get_metric(name: str) -> Optional[BaseMetric]:
    return METRIC_REGISTRY.get(name)


def available_metrics() -> list[dict]:
    return [
        {
            "name": name,
            "label": m.label,
            "description": m.description,
            "dependencies": m.dependencies,
            "available": m.available(),
            "language": (getattr(m, "descriptor", {}) or {}).get("language", "python"),
            "abi": (getattr(m, "descriptor", {}) or {}).get("abi"),
            "library": (getattr(m, "descriptor", {}) or {}).get("library"),
            "native": bool(getattr(m, "native_plugin", False)),
            "runtime_isolation": "separate_process" if bool(getattr(m, "native_plugin", False)) else "in_process",
        }
        for name, m in METRIC_REGISTRY.items()
    ]


def _is_enabled(name: str, config: dict) -> bool:
    if not config.get(f"enable_{name}", True):
        return False
    legacy = _LEGACY_KEYS.get(name)
    if legacy is not None and not config.get(legacy, True):
        return False
    return True


def compute_all(ctx: MetricContext, config: Optional[dict] = None) -> dict:
    """按注册表计算所有可用指标，返回 ``{name: value}``。

    跳过：被 ``config`` 的 ``enable_{name}=False`` 禁用、依赖缺失、或所需输入
    不满足的指标。每个指标计算异常时安全跳过。
    """
    config = config or {}
    results: dict[str, float] = {}
    for name, metric in METRIC_REGISTRY.items():
        if not _is_enabled(name, config):
            continue
        if not metric.available():
            continue
        if not metric.satisfied(ctx):
            continue
        try:
            value = metric.compute(ctx)
        except Exception:  # pylint: disable=broad-exception-caught
            value = None
        if value is not None:
            results[name] = value
    return results


def discover_metrics(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义指标。

    YAML 形如::

        name: my_metric
        label: My Metric
        description: 自定义指标
        class_path: my_package.my_module.MyMetric
        dependencies: []

    返回成功注册的指标数量。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent / "configs" / "metrics"
    if not search_dir.exists():
        return 0
    count = 0
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict):
            continue
        try:
            if is_native_descriptor(data):
                metric = NativeMetric(data, descriptor_path=yaml_file)
            else:
                if "class_path" not in data:
                    continue
                cls = import_plugin_class(data["class_path"], search_dir)
                metric = cls()
                if data.get("name"):
                    metric.name = data["name"]
                if data.get("label"):
                    metric.label = data["label"]
                if data.get("description"):
                    metric.description = data["description"]
                if data.get("dependencies"):
                    metric.dependencies = data["dependencies"]
                if data.get("requires"):
                    metric.requires = set(data["requires"] or [])
            old = METRIC_REGISTRY.get(metric.name)
            if old is not None and old is not metric and hasattr(old, "close"):
                try:
                    old.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            register_metric(metric)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
