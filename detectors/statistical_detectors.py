"""Built-in statistical detectors using detector-owned threshold strategies."""
from __future__ import annotations

import math
from typing import List

from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD


class StatisticalDetectorBase(BaseDetector):
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    """Shared plumbing for built-in statistical detectors."""

    default_threshold_method = "fixed"

    def __init__(self, config: dict):
        super().__init__(config)
        self._feature_detectors: list[StatisticalDetectorBase] = []
        self._train_stats: dict = {}

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        dimensions = len(train_X[0]) if train_X is not None and len(train_X) else 1
        self._feature_detectors = []
        if dimensions > 1:
            for index in range(dimensions):
                # Each feature gets independent training statistics; max scores
                # correspond to an OR-style anomaly decision across channels.
                detector = type(self)(dict(self.config))
                # A sibling instance shares this class-owned private fit hook.
                detector._fit_values([float(row[index]) for row in train_X])  # pylint: disable=protected-access
                self._feature_detectors.append(detector)
            self._train_stats = {'feature_count': dimensions, 'aggregation': 'max_anomaly_score'}
        else:
            values = [float(row[0]) for row in train_X] if train_X is not None and len(train_X) else []
            self._fit_values(values)

        reference_X = valid_X if valid_X is not None and len(valid_X) > 0 else train_X
        reference_scores = self.score(reference_X) if reference_X is not None and len(reference_X) > 0 else []
        self.fit_threshold(
            reference_scores,
            default_method="fixed",
            config={"threshold": self._default_decision_threshold()},
        )

    def _fit_values(self, values: List[float]) -> None:
        raise NotImplementedError

    def _default_decision_threshold(self) -> float:
        raise NotImplementedError

    def score(self, test_X) -> List[float]:
        if test_X is None or len(test_X) == 0:
            return []
        children = getattr(self, '_feature_detectors', [])
        if children:
            # The per-feature siblings share the same internal scoring hook.
            return [max(float(detector._score_one(float(row[index])))  # pylint: disable=protected-access
                        for index, detector in enumerate(children)) for row in test_X]
        values = [float(row[0]) for row in test_X]
        return [float(self._score_one(v)) for v in values]

    def _score_one(self, value: float) -> float:
        raise NotImplementedError

    @property
    def train_stats(self) -> dict:
        return getattr(self, "_train_stats", {})


class ThreeSigmaDetector(StatisticalDetectorBase):  # pylint: disable=abstract-method
    """3σ detector: score = |x - mean| / std, default score threshold = sigma."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.sigma = float(config.get("sigma", 3.0))
        self.mean = 0.0
        self.std = 0.0

    def _fit_values(self, values: List[float]) -> None:
        n = len(values)
        mean = sum(values) / n if n else 0.0
        var = sum((x - mean) ** 2 for x in values) / n if n else 0.0
        std = math.sqrt(var)
        self.mean = mean
        self.std = std
        self._train_stats = {"mean": mean, "std": std, "sigma": self.sigma}

    def _score_one(self, value: float) -> float:
        if self.std == 0:
            return 0.0
        return abs(value - self.mean) / self.std

    def _default_decision_threshold(self) -> float:
        return self.sigma


class ZScoreDetector(ThreeSigmaDetector):  # pylint: disable=abstract-method
    """Z-Score detector: like 3σ but defaults to 2.5σ."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.sigma = float(config.get("sigma", 2.5))


class IQRDetector(StatisticalDetectorBase):  # pylint: disable=abstract-method
    """IQR detector: score is distance beyond Q1/Q3 divided by IQR."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.k = float(config.get("k", 1.5))
        self.q1 = 0.0
        self.q3 = 0.0
        self.iqr = 0.0

    def _fit_values(self, values: List[float]) -> None:
        s = sorted(values)
        n = len(s)
        q1 = s[n // 4] if n else 0.0
        q3 = s[3 * n // 4] if n else 0.0
        iqr = q3 - q1
        self.q1 = q1
        self.q3 = q3
        self.iqr = iqr
        self._train_stats = {"q1": q1, "q3": q3, "iqr": iqr, "k": self.k}

    def _score_one(self, value: float) -> float:
        if self.iqr == 0:
            return 0.0
        if value > self.q3:
            return (value - self.q3) / self.iqr
        if value < self.q1:
            return (self.q1 - value) / self.iqr
        return 0.0

    def _default_decision_threshold(self) -> float:
        return self.k
