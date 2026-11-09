"""Web metrics facade: evaluate against the test table's real ``label`` column.

Metric formulas and registered metric plugins live in :mod:`evaluator.metrics`.
This facade keeps the existing Web dependency-injection entry point while
requiring ground-truth labels; it never constructs 3σ pseudo-labels.
"""
from __future__ import annotations

from typing import Sequence

from evaluator.metrics import MetricsResult, compute_supervised


class MetricsEngine:
    """Calculate supervised metrics from detector decisions and test labels."""

    def __init__(self, sigma: float = 3.0, round_ndigits: int = 6) -> None:
        # ``sigma`` is accepted for compatibility with existing container setup;
        # it no longer controls Web evaluation ground truth.
        self.sigma = sigma
        self.round_ndigits = round_ndigits

    def compute_metrics(
        self,
        *,
        y_true: Sequence[int],
        pred_label: Sequence[int] | None,
        algorithm_scores: Sequence[float] | None = None,
        score_semantics: str = "anomaly_score",
        decision_threshold: float | None = None,
        train_values: Sequence[float] | None = None,
        test_values: Sequence[float] | None = None,
    ) -> MetricsResult:
        if not y_true:
            raise ValueError("评估失败：测试集必须提供非空的真实 label 标签")
        if pred_label is None or len(pred_label) != len(y_true):
            raise ValueError("评估失败：检测器的预测标签数量与测试集 label 数量不一致")

        scores = (
            algorithm_scores
            if score_semantics not in {"fixed_threshold", "label"} and algorithm_scores
            else None
        )
        return compute_supervised(
            y_true=y_true,
            pred_label=pred_label,
            anomaly_score=scores,
            train_values=train_values,
            test_values=test_values,
            decision_threshold=decision_threshold,
            config={"mode": "supervised"},
        )


__all__ = ["MetricsEngine", "MetricsResult"]
