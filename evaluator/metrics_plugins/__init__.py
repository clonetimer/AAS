"""评估指标插件体系（与 detectors 同构）。

- ``base.BaseMetric``：指标插件契约。
- ``registry``：注册表 + 自动计算 + YAML 自动发现。
- ``builtin``：内置 7 个标准指标（precision/recall/f1/accuracy/roc_auc/pr_auc/false_alarm_rate）。

导入本包即完成内置注册，并自动发现 ``configs/metrics`` 下的自定义指标（丢文件即生效）。
"""

from .base import BaseMetric, MetricContext
from .registry import (
    METRIC_REGISTRY,
    available_metrics,
    compute_all,
    discover_metrics,
    get_metric,
    register_metric,
)
from . import builtin  # noqa: F401  (触发内置指标注册)

# 自动发现 configs/metrics 下的自定义指标
discover_metrics()
