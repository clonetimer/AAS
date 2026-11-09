from __future__ import annotations

# pylint: disable=protected-access  # Regression tests intentionally inspect the detector threshold internals and partial contract implementations.

import numpy as np
import pytest

from detectors.base import BaseDetector
from detectors.decision import (
    DECISION_MODE_LABEL,
    DECISION_MODE_NATIVE,
    DecisionBatch,
)
from detectors.isolation_forest_detector import IsolationForestDetector
from detectors.loader import detect_with_detector
from detectors.registry import get_detector_config_schema
from evaluator.metrics import compute_pseudo_label


# These fakes implement only hooks used by their decision mode.
class NativeToyDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_NATIVE
    supported_decision_modes = (DECISION_MODE_NATIVE,)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        return None

    def score(self, test_X):
        return [float(row[0]) for row in test_X]

    def native_predict(self, scores=None, test_X=None):
        # Deliberately disagree with any generic score>=threshold rule so the
        # test proves native labels are authoritative.
        labels = [1 if i % 2 == 0 else 0 for i in range(len(test_X))]
        return DecisionBatch(labels=labels, threshold=999.0)


class LabelOnlyDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_LABEL
    supported_decision_modes = (DECISION_MODE_LABEL,)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        return None

    def label_predict(self, test_X):
        return [1 if float(row[0]) > 0 else 0 for row in test_X]


def test_native_mode_is_the_only_final_decision_authority():
    detector = NativeToyDetector({"decision_mode": "native"})
    data = np.array([[0.1], [100.0], [0.2]])
    result = detector.detect(data)

    assert result["decision_mode"] == "native"
    assert result["pred_label"] == [1, 0, 1]
    assert result["threshold"] == 999.0
    assert detector._threshold_strategy is None


def test_native_or_label_mode_rejects_generic_threshold_configuration():
    with pytest.raises(ValueError, match="cannot use generic threshold"):
        NativeToyDetector({"decision_mode": "native", "threshold_method": "quantile"})

    with pytest.raises(ValueError, match="cannot use generic threshold"):
        LabelOnlyDetector({"decision_mode": "label", "threshold_alpha": 0.2})


def test_label_mode_can_produce_complete_result_without_score_or_threshold():
    detector = LabelOnlyDetector({"decision_mode": "label"})
    data = np.array([[-1.0], [2.0], [0.0]])
    result = detector.detect(data)

    assert result["decision_mode"] == "label"
    assert result["anomaly_score"] is None
    assert result["threshold"] is None
    assert result["thresholds"] == []
    assert result["pred_label"] == [0, 1, 0]


def test_state_safe_detection_preserves_detector_state_but_returns_decisions():
    class StatefulLabel(LabelOnlyDetector):  # pylint: disable=abstract-method
        def __init__(self, config):
            super().__init__(config)
            self.counter = 0

        def label_predict(self, test_X):
            self.counter += len(test_X)
            return [0 for _ in test_X]

        def export_evaluation_state(self):
            state = super().export_evaluation_state()
            state["counter"] = self.counter
            return state

        def restore_evaluation_state(self, state):
            self.counter = state["counter"]
            super().restore_evaluation_state(state)

    detector = StatefulLabel({})
    result = detect_with_detector(detector, np.array([[1.0], [2.0]]), preserve_state=True)
    assert result["pred_label"] == [0, 0]
    assert detector.counter == 0


def test_pseudo_label_metrics_accept_final_labels_without_scores():
    metrics = compute_pseudo_label(
        train_values=[0.0, 0.1, -0.1, 0.05],
        test_values=[0.0, 5.0],
        algorithm_scores=None,
        score_semantics="label",
        pred_label=[0, 1],
    )
    assert metrics.total_samples == 2
    assert metrics.accuracy == 1.0
    assert metrics.threshold is None


def test_isolation_forest_score_threshold_disables_sklearn_native_cutoff():
    detector = IsolationForestDetector({
        "decision_mode": "score_threshold",
        "contamination": 0.25,
        "threshold_method": "quantile",
        "random_state": 3,
        "n_estimators": 20,
    })
    assert detector.model.get_params()["contamination"] == "auto"
    train = np.array([[0.0], [0.1], [0.2], [0.3], [10.0], [0.15]])
    detector.fit(train)
    result = detector.detect(np.array([[0.1], [20.0]]))
    assert result["decision_mode"] == "score_threshold"
    assert detector._threshold_strategy is not None


def test_isolation_forest_native_uses_sklearn_predict_and_no_generic_strategy():
    detector = IsolationForestDetector({
        "decision_mode": "native",
        "contamination": 0.25,
        "random_state": 7,
        "n_estimators": 20,
    })
    assert detector.model.get_params()["contamination"] == 0.25
    train = np.array([[0.0], [0.1], [0.2], [0.3], [10.0], [0.15], [0.18], [0.21]])
    detector.fit(train)
    test = np.array([[0.1], [20.0], [0.2]])
    expected = [1 if int(v) == -1 else 0 for v in detector.model.predict(test)]
    result = detector.detect(test)

    assert result["decision_mode"] == "native"
    assert result["pred_label"] == expected
    assert detector._threshold_strategy is None
    assert result["threshold"] == pytest.approx(-float(detector.model.offset_))


def test_isolation_forest_native_rejects_ambiguous_generic_threshold():
    with pytest.raises(ValueError, match="cannot use generic threshold"):
        IsolationForestDetector({
            "decision_mode": "native",
            "contamination": 0.1,
            "threshold_method": "quantile",
        })


def test_detector_schema_exposes_decision_mode_and_conditional_threshold_controls():
    schema = get_detector_config_schema("isolation_forest_detector")
    params = {item["name"]: item for item in schema["parameters"]}

    assert params["decision_mode"]["options"] == ["score_threshold", "native"]
    assert params["threshold_method"]["visible_when"] == {
        "name": "decision_mode",
        "value": "score_threshold",
    }
    assert schema["decision_mode"] == "score_threshold"


def test_score_only_detectors_do_not_offer_native_or_label_modes():
    schema = get_detector_config_schema("mlp_detector")
    params = {item["name"]: item for item in schema["parameters"]}
    assert params["decision_mode"]["options"] == ["score_threshold"]


def test_framework_owned_decision_entrypoints_cannot_be_overridden():
    class RogueDetector(BaseDetector):  # pylint: disable=abstract-method
        def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
            return None

        def score(self, test_X):
            return [0.0 for _ in test_X]

        def predict(self, scores=None, test_X=None):  # must never become a second authority
            return [1 for _ in test_X], -999.0

    with pytest.raises(TypeError, match="must not override framework-owned predict"):
        RogueDetector({})
