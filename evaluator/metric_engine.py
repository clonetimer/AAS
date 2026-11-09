from __future__ import annotations



class MetricEngine:
    """Backwards-compatible facade for supervised evaluation.

    The actual math now lives in :mod:`evaluator.metrics` (single source of
    truth shared with the Web path). This thin shell keeps the existing
    ``orchestrator`` / ``test_metric_engine`` call sites unchanged.
    """

    def compute_all(self, y_true, anomaly_score=None, pred_label=None, config=None) -> dict:
        from evaluator.metrics import compute_supervised_metrics

        return compute_supervised_metrics(y_true, anomaly_score, pred_label, config)
