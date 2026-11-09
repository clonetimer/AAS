"""Detector plugin template.

Copy this file to ``detectors/<your_name>.py`` and pair it with a YAML descriptor
under ``configs/detectors/``. This template is a ``score_threshold`` detector:
it must implement ``fit`` and ``score`` while ``BaseDetector`` owns the final
threshold decision. For algorithm-native decisions declare ``decision_mode=native``
and implement ``native_predict``; for direct labels use ``decision_mode=label`` and
implement ``label_predict``. Never combine more than one final decision path.
"""
from __future__ import annotations

import numpy as np

from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD


class TemplateDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    def __init__(self, config: dict):
        super().__init__(config)
        self.center_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        data = np.asarray(train_X, dtype=float)
        if data.ndim != 2 or len(data) == 0:
            raise ValueError("train_X must be a non-empty 2D array")
        self.center_ = np.mean(data, axis=0)
        self.scale_ = np.std(data, axis=0)
        self.scale_[self.scale_ == 0] = 1.0

    def score(self, test_X) -> list[float]:
        if self.center_ is None or self.scale_ is None:
            raise RuntimeError("fit() must be called before score()")
        data = np.asarray(test_X, dtype=float)
        if data.ndim != 2:
            raise ValueError("test_X must be a 2D array")
        z = np.abs((data - self.center_) / self.scale_)
        return np.max(z, axis=1).astype(float).tolist()
