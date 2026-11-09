from __future__ import annotations

from detectors.dummy_detector import DummyDetector
from detectors.thresholding import SUPPORTED_THRESHOLD_METHODS
from evaluator.metrics import compute_pseudo_label


def test_online_and_adaptive_thresholds_are_internal_detector_strategies():
    assert "online_quantile" in SUPPORTED_THRESHOLD_METHODS
    assert "adaptive_ewma" in SUPPORTED_THRESHOLD_METHODS


def test_online_quantile_uses_preupdate_threshold_and_skips_anomaly_updates_by_default():
    detector = DummyDetector({
        "threshold_method": "online_quantile",
        "threshold_window": 4,
        "contamination": 0.25,
    })
    detector.fit([[0.0], [1.0], [2.0], [3.0]])

    # Training-score threshold is 1.5 with the historical floor quantile.
    labels, _ = detector.predict([10.0, 1.5])
    trace = detector.get_threshold_trace()

    assert labels[0] == 1
    assert trace[0] == 1.5
    # 10.0 is anomalous and must not poison the next threshold by default.
    assert trace[1] == 1.5


def test_adaptive_ewma_threshold_moves_after_normal_stream_samples():
    detector = DummyDetector({
        "threshold_method": "adaptive_ewma",
        "threshold_alpha": 0.5,
        "threshold_sigma": 3.0,
    })
    detector.fit([[0.0], [1.0], [2.0], [3.0]])
    before = detector.get_threshold()

    # 2.0 is below the calibrated threshold, so it is considered normal and
    # participates in the EWMA update. The threshold must move for the next item.
    labels, _ = detector.predict([2.0])
    assert labels == [0]
    assert detector.get_threshold() != before


def test_pseudo_label_evaluation_uses_detector_labels_for_adaptive_thresholds():
    # If evaluator ignored pred_label and rebuilt decisions from final threshold,
    # this deliberately contradictory label vector would not be preserved.
    result = compute_pseudo_label(
        train_values=[0.0, 0.0, 0.0, 0.0],
        test_values=[0.0, 10.0],
        algorithm_scores=[0.1, 0.2],
        decision_threshold=999.0,
        pred_label=[0, 1],
    )
    assert result.tp == 1
    assert result.tn == 1


def test_algorithm_runner_carries_detector_labels_and_threshold_trace_into_evaluation_result():
    from scripts.core.algorithm_runner import AlgorithmRunner

    runner = AlgorithmRunner()
    result = runner.run(
        "dummy_detector",
        train_values=[0.0, 1.0, 2.0, 3.0],
        test_values=[10.0, 1.5],
        algorithm_params={
            "threshold_method": "online_quantile",
            "threshold_window": 4,
            "contamination": 0.25,
        },
    )
    assert len(result.pred_labels) == 2
    assert len(result.decision_thresholds) == 2
    assert result.pred_labels[0] == 1
    assert result.decision_thresholds[0] == result.decision_thresholds[1]
    # Evaluation simulates online adaptation (final threshold differs) but the
    # detector is restored before model persistence, so test data cannot leak
    # into the saved online-threshold state.
    assert result.decision_threshold != result.detector.get_threshold()
    assert result.detector.get_threshold() == result.decision_thresholds[0]


def test_online_threshold_state_survives_pickle_model_persistence():
    import pickle

    detector = DummyDetector({
        "threshold_method": "online_quantile",
        "threshold_window": 4,
        "contamination": 0.25,
    })
    detector.fit([[0.0], [1.0], [2.0], [3.0]])
    detector.predict([1.0])  # normal score updates the rolling window
    before = detector.get_threshold()

    restored = pickle.loads(pickle.dumps(detector))
    assert restored.get_threshold() == before
    assert restored.export_threshold_state() == detector.export_threshold_state()
