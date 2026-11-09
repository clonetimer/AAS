from __future__ import annotations

from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD


class DummyDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    """Deterministic sample detector for baseline evaluation."""

    default_threshold_method = "fixed"

    def __init__(self, config: dict):
        super().__init__(config)
        self.scale = float(config.get("scale", 1.0))
        self.offset = float(config.get("offset", 0.0))
        self.column_index = int(config.get("column_index", 0))
        self.mean_score = 0.0
        self.threshold_value = float(config.get("threshold", 0.5))

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        if train_X is None or len(train_X) == 0:
            self.mean_score = 0.0
            reference_scores: list[float] = []
        else:
            values = [row[self.column_index] for row in train_X]
            self.mean_score = sum(values) / len(values)
            reference_X = valid_X if valid_X is not None and len(valid_X) > 0 else train_X
            reference_scores = self.score(reference_X)
        self.fit_threshold(
            reference_scores,
            default_method="fixed",
            config={"threshold": self.threshold_value},
        )

    def score(self, test_X) -> list[float]:
        scores = []
        for row in test_X:
            raw = row[self.column_index]
            score = abs(raw - self.mean_score) * self.scale + self.offset
            scores.append(float(score))
        return scores

    def get_meta(self) -> dict:
        meta = super().get_meta()
        meta["name"] = "dummy_detector"
        return meta
