"""内置预处理算子。"""

from __future__ import annotations

from ._operators_impl import (
    fill_missing_values,
    normalize_data,
    repair_outliers,
    smooth_data,
)
from .base import BasePreprocessOperator
from .registry import register_operator


class FillMissingOperator(BasePreprocessOperator):
    name = "fill_missing"
    label = "填充缺失值"
    description = "使用前向 + 后向填充补齐缺失值"
    order = 10

    def enabled(self, config: dict) -> bool:
        return config.get("fill_missing", True)

    def apply(self, rows, config):
        return fill_missing_values(rows)


class RepairOutlierOperator(BasePreprocessOperator):
    name = "repair_outlier"
    label = "修复异常值"
    description = "3σ 原则将异常值替换为中位数"
    order = 20

    def enabled(self, config: dict) -> bool:
        return config.get("repair_outlier", True)

    def apply(self, rows, config):
        rows, _ = repair_outliers(rows, config.get("sigma_multiplier", 3.0))
        return rows


class SmoothOperator(BasePreprocessOperator):
    name = "smooth"
    label = "数据平滑"
    description = "移动平均平滑"
    order = 30

    def enabled(self, config: dict) -> bool:
        return config.get("smooth", True)

    def apply(self, rows, config):
        return smooth_data(rows, config.get("smooth_window", 3))


class NormalizeOperator(BasePreprocessOperator):
    name = "normalize"
    label = "归一化"
    description = "min_max / zscore 归一化"
    order = 40

    def enabled(self, config: dict) -> bool:
        return config.get("normalize_method", "min_max") != "none"

    def apply(self, rows, config):
        return normalize_data(rows, config.get("normalize_method", "min_max"))


def _register_builtins() -> None:
    for op in (
        FillMissingOperator(),
        RepairOutlierOperator(),
        SmoothOperator(),
        NormalizeOperator(),
    ):
        register_operator(op)


_register_builtins()
