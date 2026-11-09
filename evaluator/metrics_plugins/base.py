"""指标插件契约与统一上下文。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class MetricContext:
    """评估指标计算的统一上下文。"""

    y_true: Optional[Sequence[int]] = None
    anomaly_score: Optional[Sequence[float]] = None
    pred_label: Optional[Sequence[int]] = None
    train_values: Optional[Sequence[float]] = None
    test_values: Optional[Sequence[float]] = None
    config: dict = field(default_factory=dict)

    def confusion(self) -> tuple[int, int, int, int]:
        from evaluator.metrics import _confusion

        return _confusion(self.y_true or [], self.pred_label or [])


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


class BaseMetric(ABC):
    """指标插件契约。

    继承后实现 :meth:`compute` 即可作为一个指标被注册并参与计算。
    ``requires`` 声明该指标依赖的输入（labels/scores/preds/...），缺失依赖时
    自动跳过；``dependencies`` 声明所需第三方库，缺失时自动置灰。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    dependencies: list[str] = []
    requires: set[str] = set()

    def available(self) -> bool:
        for dep in self.dependencies:
            try:
                __import__(dep)
            except ImportError:
                return False
        return True

    def satisfied(self, ctx: MetricContext) -> bool:
        need_map = {
            "labels": ctx.y_true is not None,
            "scores": ctx.anomaly_score is not None,
            "preds": ctx.pred_label is not None,
            "train_values": ctx.train_values is not None,
            "test_values": ctx.test_values is not None,
        }
        return all(need_map.get(r, False) for r in self.requires)

    @abstractmethod
    def compute(self, ctx: MetricContext) -> Optional[float]:
        raise NotImplementedError
