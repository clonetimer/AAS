"""Unified evaluation metrics service (single source of truth).

Both CLI and Web supervised evaluation use actual ground-truth labels. The
legacy pseudo-label helper remains available for explicit callers, but is no
longer used by Web training or published-model evaluation. This eliminates the duplicated
`evaluator/metric_engine.py` vs `scripts/core/metrics_engine.py` logic, the
`f1` vs `f1_score` field mismatch, and the magic-number heuristic
(`threshold_max < 10`) used by the old pseudo-label engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from detectors.thresholding import quantile_threshold
from evaluator.metrics_plugins.base import MetricContext
from evaluator.metrics_plugins.registry import compute_all


@dataclass
class MetricsResult:
    """Unified metrics result container.

    Both evaluation strategies share this exact field schema so that CLI and
    Web consumers get a consistent structure for display and storage.
    """

    mode: str = "supervised"
    tp: int = 0
    tn: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    accuracy: float = 0.0
    roc_auc: Optional[float] = None
    pr_auc: Optional[float] = None
    false_alarm_rate: Optional[float] = None
    threshold: Optional[float] = None
    total_samples: int = 0
    anomaly_count: int = 0
    normal_count: int = 0
    # 自定义指标插件的结果放在 extras 中，但序列化时平铺到顶层，保证前端、
    # 报告和历史数据无需认识新的嵌套结构即可自动获得扩展指标。
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, round_ndigits: int = 6) -> dict:
        data = {
            key: (round(value, round_ndigits) if isinstance(value, float) else value)
            for key, value in self.__dict__.items()
            if key != "extras"
        }
        for key, value in self.extras.items():
            if key in data:
                continue
            data[key] = round(value, round_ndigits) if isinstance(value, float) else value
        return data


def _confusion(y_true: Sequence[int], pred_label: Sequence[int]) -> tuple[int, int, int, int]:
    tp = fp = fn = tn = 0
    for truth, pred in zip(y_true, pred_label):
        if truth == 1 and pred == 1:
            tp += 1
        elif truth == 0 and pred == 1:
            fp += 1
        elif truth == 1 and pred == 0:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def compute_supervised_metrics(
    y_true: Sequence[int],
    anomaly_score: Optional[Sequence[float]] = None,
    pred_label: Optional[Sequence[int]] = None,
    config: Optional[dict] = None,
    *,
    train_values: Optional[Sequence[float]] = None,
    test_values: Optional[Sequence[float]] = None,
) -> dict:
    """按指标插件注册表计算有标签评估指标，返回 ``{name: value}``。

    内置指标来自 ``evaluator.metrics_plugins.builtin``，并自动包含
    ``configs/metrics`` 下发现的自定义指标（丢文件即生效）。
    """
    config = config or {}
    ctx = MetricContext(
        y_true=y_true,
        anomaly_score=anomaly_score,
        pred_label=pred_label,
        train_values=train_values,
        test_values=test_values,
        config=config,
    )
    return compute_all(ctx, config)


def compute_supervised(
    y_true: Sequence[int],
    anomaly_score: Optional[Sequence[float]] = None,
    pred_label: Optional[Sequence[int]] = None,
    config: Optional[dict] = None,
    *,
    train_values: Optional[Sequence[float]] = None,
    test_values: Optional[Sequence[float]] = None,
    decision_threshold: Optional[float] = None,
) -> MetricsResult:
    """Supervised evaluation with ground-truth labels.

    Metric math now flows through the metric plugin registry
    (``evaluator.metrics_plugins``); this wrapper reconstructs the backward
    compatible :class:`MetricsResult` object for existing callers.
    """
    config = config or {}
    if anomaly_score is not None and len(anomaly_score) != len(y_true):
        raise ValueError(
            f"Length mismatch: anomaly_score ({len(anomaly_score)}) != y_true ({len(y_true)})"
        )
    if pred_label is not None and len(pred_label) != len(y_true):
        raise ValueError(
            f"Length mismatch: pred_label ({len(pred_label)}) != y_true ({len(y_true)})"
        )

    anomaly_count = sum(1 for label in y_true if label == 1)
    result = MetricsResult(
        mode="supervised",
        total_samples=len(y_true),
        anomaly_count=anomaly_count,
        normal_count=len(y_true) - anomaly_count,
        threshold=decision_threshold,
    )
    if pred_label is not None:
        tp, fp, fn, tn = _confusion(y_true, pred_label)
        result.tp, result.fp, result.fn, result.tn = tp, fp, fn, tn

    values = compute_supervised_metrics(
        y_true, anomaly_score, pred_label, config,
        train_values=train_values, test_values=test_values,
    )
    for field_name in (
        "precision",
        "recall",
        "f1",
        "accuracy",
        "roc_auc",
        "pr_auc",
        "false_alarm_rate",
    ):
        if field_name in values:
            setattr(result, field_name, values[field_name])
    standard_fields = {
        "precision", "recall", "f1", "accuracy", "roc_auc", "pr_auc",
        "false_alarm_rate", "threshold", "anomaly_count", "normal_count",
    }
    result.extras = {key: value for key, value in values.items() if key not in standard_fields}
    return result


def _auc_roc(y_true: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    positives = sum(1 for v in y_true if v == 1)
    negatives = sum(1 for v in y_true if v == 0)
    if positives == 0 or negatives == 0:
        return None
    ranked = sorted(zip(scores, y_true), key=lambda item: item[0])
    rank_sum = 0.0
    for index, (_, label) in enumerate(ranked, start=1):
        if label == 1:
            rank_sum += index
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _auc_pr(y_true: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    positives = sum(1 for v in y_true if v == 1)
    if positives == 0:
        return None
    pairs = sorted(zip(scores, y_true), key=lambda item: item[0], reverse=True)
    tp = fp = 0
    points: list[tuple[float, float]] = [(0.0, 1.0)]
    for _, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1
        recall = tp / positives
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        points.append((recall, precision))
    area = 0.0
    prev_recall, prev_precision = points[0]
    for recall, precision in points[1:]:
        area += (recall - prev_recall) * ((precision + prev_precision) / 2)
        prev_recall, prev_precision = recall, precision
    return area


def compute_pseudo_label(
    train_values: Sequence[float],
    test_values: Sequence[float],
    algorithm_scores: Optional[Sequence[float]],
    score_semantics: str = "anomaly_score",
    sigma: float = 3.0,
    contamination: float = 0.05,
    decision_threshold: Optional[float] = None,
    pred_label: Optional[Sequence[int]] = None,
) -> MetricsResult:
    """Pseudo-label evaluation without ground-truth labels.

    Uses the training set 3-sigma rule as a proxy for "real" anomalies, then
    derives predictions from the algorithm output to build a confusion matrix.

    Fixes two defects of the old ``scripts/core/metrics_engine``:
    1. No more ``threshold_max < 10`` magic number to guess score semantics;
       ``score_semantics`` is declared explicitly instead.
    2. The decision threshold is no longer the score mean (which flagged ~half
       of samples). A detector-fitted threshold is reused when available; otherwise
       the ``1 - contamination`` score quantile is used as a fallback.
    """
    if not train_values:
        raise ValueError("train_values must not be empty")
    if not test_values:
        raise ValueError("test_values must not be empty")
    score_values = list(algorithm_scores or [])
    if pred_label is None and not score_values:
        raise ValueError("algorithm_scores must not be empty when pred_label is absent")
    if score_values and len(test_values) != len(score_values):
        raise ValueError(
            f"test_values ({len(test_values)}) != algorithm_scores ({len(score_values)})"
        )
    if pred_label is not None and len(pred_label) != len(test_values):
        raise ValueError(
            f"pred_label ({len(pred_label)}) != test_values ({len(test_values)})"
        )

    train_mean = sum(train_values) / len(train_values)
    train_std = (
        sum((x - train_mean) ** 2 for x in train_values) / len(train_values)
    ) ** 0.5
    boundary = sigma * train_std
    pseudo_labels = [1 if abs(v - train_mean) > boundary else 0 for v in test_values]

    if pred_label is not None:
        # The detector owns the decision rule. This is essential for online or
        # adaptive thresholds because a single scalar threshold cannot recreate
        # the per-sample decisions after the threshold state has moved.
        preds = [1 if int(v) else 0 for v in pred_label]
        threshold = float(decision_threshold) if decision_threshold is not None else None
    elif score_semantics == "fixed_threshold":
        preds = [1 if v > s else 0 for v, s in zip(test_values, score_values)]
        threshold = None
    else:
        # Compatibility fallback for legacy callers that supplied scores but no
        # detector labels. New training/evaluation flows always pass pred_label.
        if decision_threshold is None:
            threshold = quantile_threshold(score_values, contamination=contamination)
        else:
            threshold = float(decision_threshold)
        preds = [1 if s >= threshold else 0 for s in score_values]

    tp, fp, fn, tn = _confusion(pseudo_labels, preds)

    # Web 伪标签路径也走同一指标插件注册表。这样安装一个 BaseMetric 插件后，
    # 不仅 CLI 会计算它，Web 训练结果也会自动带上该指标，插件才真正成为
    # 业务能力而不是“插件管理页里能看到的条目”。固定阈值算法没有可比较的
    # anomaly score，因此不向需要 scores 的指标伪造输入。
    metric_ctx = MetricContext(
        y_true=pseudo_labels,
        anomaly_score=score_values if score_values and score_semantics not in {"fixed_threshold", "label"} else None,
        pred_label=preds,
        train_values=train_values,
        test_values=test_values,
        config={"mode": "pseudo_label"},
    )
    plugin_values = compute_all(metric_ctx, metric_ctx.config)

    result = MetricsResult(
        mode="pseudo_label",
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
        precision=float(plugin_values.get("precision", _safe_div(tp, tp + fp))),
        recall=float(plugin_values.get("recall", _safe_div(tp, tp + fn))),
        accuracy=float(plugin_values.get("accuracy", _safe_div(tp + tn, len(pseudo_labels)))),
        roc_auc=plugin_values.get("roc_auc"),
        pr_auc=plugin_values.get("pr_auc"),
        false_alarm_rate=plugin_values.get("false_alarm_rate"),
        threshold=threshold,
        total_samples=len(test_values),
        anomaly_count=sum(pseudo_labels),
        normal_count=len(pseudo_labels) - sum(pseudo_labels),
    )
    result.f1 = float(plugin_values.get(
        "f1",
        _safe_div(2 * result.precision * result.recall, result.precision + result.recall),
    ))

    standard_fields = {
        "precision", "recall", "f1", "accuracy", "roc_auc", "pr_auc",
        "false_alarm_rate", "threshold", "anomaly_count", "normal_count",
    }
    result.extras = {
        key: value for key, value in plugin_values.items() if key not in standard_fields
    }
    return result


class MetricsService:
    """Unified evaluation service facade shared by CLI and Web paths."""

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}

    def supervised(
        self,
        y_true: Sequence[int],
        anomaly_score: Optional[Sequence[float]] = None,
        pred_label: Optional[Sequence[int]] = None,
        config: Optional[dict] = None,
    ) -> dict:
        merged = {**self.config, **(config or {})}
        return {
            **compute_supervised_metrics(y_true, anomaly_score, pred_label, merged),
            "mode": "supervised",
        }

    def pseudo_label(
        self,
        train_values: Sequence[float],
        test_values: Sequence[float],
        algorithm_scores: Optional[Sequence[float]],
        score_semantics: str = "anomaly_score",
        sigma: float = 3.0,
        contamination: float = 0.05,
        decision_threshold: Optional[float] = None,
        pred_label: Optional[Sequence[int]] = None,
    ) -> dict:
        return compute_pseudo_label(
            train_values, test_values, algorithm_scores,
            score_semantics=score_semantics, sigma=sigma, contamination=contamination,
            decision_threshold=decision_threshold, pred_label=pred_label,
        ).to_dict()

    def evaluate(
        self,
        *,
        y_true: Optional[Sequence[int]] = None,
        anomaly_score: Optional[Sequence[float]] = None,
        pred_label: Optional[Sequence[int]] = None,
        train_values: Optional[Sequence[float]] = None,
        test_values: Optional[Sequence[float]] = None,
        algorithm_scores: Optional[Sequence[float]] = None,
        score_semantics: str = "anomaly_score",
        sigma: float = 3.0,
        contamination: float = 0.05,
        decision_threshold: Optional[float] = None,
    ) -> dict:
        """Pick a strategy based on which inputs are available."""
        if y_true is not None:
            return self.supervised(y_true, anomaly_score, pred_label)
        if train_values is not None and test_values is not None and (algorithm_scores is not None or pred_label is not None):
            return self.pseudo_label(
                train_values, test_values, algorithm_scores,
                score_semantics=score_semantics, sigma=sigma, contamination=contamination,
                decision_threshold=decision_threshold, pred_label=pred_label,
            )
        raise ValueError(
            "evaluate requires y_true, or train_values/test_values plus pred_label or algorithm_scores"
        )
