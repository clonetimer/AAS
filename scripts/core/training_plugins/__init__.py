"""训练策略插件体系（与 detectors 同构）。

- ``base.BaseTrainingStrategy``：训练策略契约（execute(...)）。
- ``registry``：注册表 + 按名称解析 + YAML 自动发现。
- ``builtin``：内置 standard 策略（算法执行 → 指标计算 → 模型保存）。

新增一种训练策略（如交叉验证 / 集成 / 分阶段训练）只需写一个子类 + 一个 YAML 描述。
"""

from .base import BaseTrainingStrategy
from .registry import (
    TRAINING_STRATEGY_REGISTRY,
    available_strategies,
    discover_strategies,
    get_strategy,
    register_strategy,
)
from . import builtin  # noqa: F401  (触发内置策略注册)

# 自动发现 configs/training_strategies 下的自定义策略
discover_strategies()
