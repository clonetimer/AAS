from __future__ import annotations

from detectors.base import BaseDetector
from detectors.decision import (
    DECISION_MODE_NATIVE,
    DECISION_MODE_SCORE_THRESHOLD,
    DecisionBatch,
)


class IsolationForestDetector(BaseDetector):  # pylint: disable=abstract-method
    """scikit-learn IsolationForest with explicit single decision authority.

    ``score_threshold`` (default)
        sklearn only produces anomaly scores. Its internal contamination cutoff
        is disabled via ``contamination='auto'`` and AAS detector-owned threshold
        strategies perform the sole final decision.

    ``native``
        sklearn ``predict()`` is authoritative. Generic threshold strategies are
        disabled; ``contamination`` controls sklearn's native offset.
    """

    default_threshold_method = "quantile"
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD, DECISION_MODE_NATIVE)

    def __init__(self, config: dict):
        super().__init__(config)
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError as exc:
            raise ImportError(
                "IsolationForestDetector requires scikit-learn. "
                "Install it with: uv add scikit-learn or pip install scikit-learn"
            ) from exc

        if self.decision_mode == DECISION_MODE_NATIVE and "threshold" in config:
            raise ValueError(
                "IsolationForest decision_mode='native' uses sklearn's native offset; "
                "a separate fixed threshold is ambiguous. Remove config.threshold or "
                "use decision_mode='score_threshold'."
            )

        self._isolation_forest_cls = IsolationForest
        self.contamination = float(config.get("contamination", 0.05))
        sklearn_contamination = (
            self.contamination if self.decision_mode == DECISION_MODE_NATIVE else "auto"
        )
        self.model = IsolationForest(
            n_estimators=int(config.get("n_estimators", 100)),
            contamination=sklearn_contamination,
            random_state=int(config.get("random_state", 42)),
        )
        self.threshold_value = float(config["threshold"]) if "threshold" in config else None

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        self.model.fit(train_X)

        if self.decision_mode == DECISION_MODE_NATIVE:
            self.threshold_value = -float(self.model.offset_)
            self._threshold = self.threshold_value
            return

        reference_X = valid_X if valid_X is not None and len(valid_X) > 0 else train_X
        reference_scores = self.score(reference_X)
        extra = {"contamination": self.contamination}
        if self.threshold_value is not None:
            extra["threshold"] = self.threshold_value
            default_method = "fixed"
        else:
            default_method = "quantile"
        self.threshold_value = self.fit_threshold(
            reference_scores,
            default_method=default_method,
            config=extra,
        )

    def score(self, test_X) -> list[float]:
        # sklearn score_samples(): larger = more normal. AAS anomaly score uses
        # the opposite direction so larger consistently means more anomalous.
        scores = [-value for value in self.model.score_samples(test_X)]
        return [float(score) for score in scores]

    def native_predict(self, scores=None, test_X=None) -> DecisionBatch:
        if test_X is None:
            raise ValueError("IsolationForest native decision requires test_X")
        raw = self.model.predict(test_X)
        labels = [1 if int(v) == -1 else 0 for v in raw]
        # sklearn: predict == -1 when score_samples < offset_. Since AAS exposes
        # -score_samples as anomaly score, the auditable equivalent cutoff is
        # -offset_. Labels still come from sklearn.predict(), never reconstructed.
        threshold = -float(self.model.offset_)
        return DecisionBatch(
            labels=labels,
            threshold=threshold,
            thresholds=[threshold for _ in labels],
        )

    def get_meta(self) -> dict:
        meta = super().get_meta()
        meta.update({
            "name": "isolation_forest_detector",
            "library": "scikit-learn",
            "sklearn_contamination": self.model.get_params().get("contamination"),
        })
        return meta
