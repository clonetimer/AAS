"""内置标准指标插件。"""

from __future__ import annotations

from .base import BaseMetric, MetricContext, _safe_div
from .registry import register_metric


class PrecisionMetric(BaseMetric):
    name = "precision"
    label = "Precision"
    description = "精确率 = TP / (TP + FP)"
    requires = {"labels", "preds"}

    def compute(self, ctx: MetricContext) -> float:
        tp, fp, _, _ = ctx.confusion()
        return _safe_div(tp, tp + fp)


class RecallMetric(BaseMetric):
    name = "recall"
    label = "Recall"
    description = "召回率 = TP / (TP + FN)"
    requires = {"labels", "preds"}

    def compute(self, ctx: MetricContext) -> float:
        tp, _, fn, _ = ctx.confusion()
        return _safe_div(tp, tp + fn)


class F1Metric(BaseMetric):
    name = "f1"
    label = "F1"
    description = "F1 = 2PR / (P + R)"
    requires = {"labels", "preds"}

    def compute(self, ctx: MetricContext) -> float:
        tp, fp, fn, _ = ctx.confusion()
        p = _safe_div(tp, tp + fp)
        r = _safe_div(tp, tp + fn)
        return _safe_div(2 * p * r, p + r)


class AccuracyMetric(BaseMetric):
    name = "accuracy"
    label = "Accuracy"
    description = "准确率 = (TP + TN) / N"
    requires = {"labels", "preds"}

    def compute(self, ctx: MetricContext) -> float:
        tp, _, _, tn = ctx.confusion()
        total = len(ctx.y_true) if ctx.y_true is not None else 0
        return _safe_div(tp + tn, total)


class FalseAlarmRateMetric(BaseMetric):
    name = "false_alarm_rate"
    label = "False Alarm Rate"
    description = "误报率 = FP / (FP + TN)"
    requires = {"labels", "preds"}

    def compute(self, ctx: MetricContext) -> float:
        _, fp, _, tn = ctx.confusion()
        return _safe_div(fp, fp + tn)


class ROCAUCMetric(BaseMetric):
    name = "roc_auc"
    label = "ROC AUC"
    description = "ROC 曲线下面积"
    requires = {"labels", "scores"}

    def compute(self, ctx: MetricContext) -> float:
        from evaluator.metrics import _auc_roc

        return _auc_roc(ctx.y_true, ctx.anomaly_score)


class PRAUCMetric(BaseMetric):
    name = "pr_auc"
    label = "PR AUC"
    description = "PR 曲线下面积"
    requires = {"labels", "scores"}

    def compute(self, ctx: MetricContext) -> float:
        from evaluator.metrics import _auc_pr

        return _auc_pr(ctx.y_true, ctx.anomaly_score)


def _register_builtins() -> None:
    for cls in (
        PrecisionMetric,
        RecallMetric,
        F1Metric,
        AccuracyMetric,
        FalseAlarmRateMetric,
        ROCAUCMetric,
        PRAUCMetric,
    ):
        register_metric(cls())


_register_builtins()
