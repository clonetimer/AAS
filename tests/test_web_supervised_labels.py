"""Regression tests: Web evaluation ground truth comes from SQLite label, never 3σ."""
from __future__ import annotations

# pylint: disable=protected-access  # The test intentionally replaces the internal runner to assert fitting was not attempted.

import sqlite3
from unittest.mock import Mock

import numpy as np
import pytest

from detectors.statistical_detectors import ZScoreDetector
from evaluator.metrics_plugins.base import BaseMetric
from evaluator.metrics_plugins.registry import METRIC_REGISTRY, register_metric
from scripts.core.data_loader import DataLoader, extract_binary_labels
from scripts.core.metrics_engine import MetricsEngine
from scripts.core.training import TrainingPipeline


@pytest.fixture(name="labeled_db_path")
def labeled_db(tmp_path):
    path = tmp_path / "web_evaluation.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE training (timestamp TEXT, value REAL, label INTEGER)")
        conn.executemany(
            "INSERT INTO training VALUES (?, ?, ?)",
            [(str(i), float(i), 0) for i in range(4)],
        )
        conn.execute("CREATE TABLE testing (timestamp TEXT, value REAL, label TEXT)")
        conn.executemany(
            "INSERT INTO testing VALUES (?, ?, ?)",
            [("t0", 0.0, "1"), ("t1", 1.0, "0"),
             ("t2", 30.0, "0"), ("t3", 2.0, "1")],
        )
    return path


def test_extract_binary_labels_aligns_with_valid_value_rows(tmp_path):
    path = tmp_path / "rows.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE testing (timestamp TEXT, value TEXT, label TEXT)")
        conn.executemany(
            "INSERT INTO testing VALUES (?, ?, ?)",
            [("t0", "1", "1"), ("t1", "not_a_number", "0"),
             ("t2", "2", "0"), ("t3", None, "1")],
        )
    bundle = DataLoader(path, train_ratio=1.0).load_and_split(table="testing")
    assert bundle.test_values == [1.0, 2.0]
    assert extract_binary_labels(bundle.test_value_rows) == [1, 0]
    assert len(bundle.test_rows) == 4  # labels from these raw rows would misalign


@pytest.mark.parametrize("raw, expected", [
    (0, 0), (1, 1), ("0", 0), ("1", 1), (" 1.0 ", 1),
    (True, 1), (False, 0), ("normal", 0), ("故障", 1),
])
def test_label_parser_normalizes_valid_binary_values(raw, expected):
    assert extract_binary_labels([{"value": 2.0, "label": raw}]) == [expected]


@pytest.mark.parametrize("bad", [None, "", "unknown", "nan", "inf", 2, -1, 0.5])
def test_label_parser_rejects_invalid_values(bad):
    with pytest.raises(ValueError, match=r"label.*无效"):
        extract_binary_labels([{"value": 2.0, "label": bad}])


def test_label_parser_rejects_missing_column():
    with pytest.raises(ValueError, match="缺少 label 列"):
        extract_binary_labels([{"value": 2.0}])


def test_supervised_metrics_use_true_labels_not_pseudo_labels():
    # All test values coincide with training mean: 3σ would mark all as normal.
    # Real test labels intentionally disagree to prove they are authoritative.
    result = MetricsEngine().compute_metrics(
        y_true=[1, 0, 1, 0],
        pred_label=[1, 1, 0, 0],
        train_values=[0.0, 0.0, 0.0],
        test_values=[0.0, 0.0, 0.0, 0.0],
        algorithm_scores=[0.9, 0.8, 0.2, 0.1],
        decision_threshold=0.5,
    )
    assert result.mode == "supervised"
    assert (result.tp, result.tn, result.fp, result.fn) == (1, 1, 1, 1)
    assert (result.precision, result.recall, result.f1, result.accuracy) == (0.5,) * 4
    assert (result.anomaly_count, result.normal_count, result.total_samples) == (2, 2, 4)
    assert result.threshold == 0.5
    assert result.roc_auc is not None


def test_label_only_detectors_keep_classification_metrics_without_scores():
    result = MetricsEngine().compute_metrics(
        y_true=[0, 1], pred_label=[0, 1],
        score_semantics="label", algorithm_scores=None,
    )
    assert result.accuracy == 1.0
    assert result.roc_auc is None
    assert result.pr_auc is None
    assert result.threshold is None


def test_supervised_metrics_preserve_custom_plugin_results():
    class CustomMetric(BaseMetric):
        name = "test_web_training_values"
        requires = {"labels", "preds", "train_values", "test_values"}

        def compute(self, ctx):
            return float(len(ctx.train_values) + len(ctx.test_values))

    before = METRIC_REGISTRY.get(CustomMetric.name)
    register_metric(CustomMetric())
    try:
        result = MetricsEngine().compute_metrics(
            y_true=[0, 1], pred_label=[0, 1],
            train_values=[1.0, 2.0, 3.0], test_values=[0.0, 10.0],
        )
        assert result.to_dict()[CustomMetric.name] == 5.0
    finally:
        METRIC_REGISTRY.pop(CustomMetric.name, None)
        if before is not None:
            METRIC_REGISTRY[CustomMetric.name] = before


def test_training_pipeline_uses_test_table_labels(labeled_db_path):
    result = TrainingPipeline(labeled_db_path).run(
        algorithm="zscore", train_table="training", test_table="testing",
        persist_model=False,
    )
    assert result.metrics.mode == "supervised"
    assert result.metrics.anomaly_count == 2
    assert result.metrics.normal_count == 2
    assert [row["label"] for row in result.result_rows] == [1, 0, 0, 1]
    assert len(result.algorithm_result.pred_labels) == 4
    pred = result.algorithm_result.pred_labels
    truth = [1, 0, 0, 1]
    assert result.metrics.tp == sum(a == 1 and b == 1 for a, b in zip(truth, pred))
    assert result.metrics.fp == sum(a == 0 and b == 1 for a, b in zip(truth, pred))
    assert result.metrics.fn == sum(a == 1 and b == 0 for a, b in zip(truth, pred))
    assert result.metrics.tn == sum(a == 0 and b == 0 for a, b in zip(truth, pred))


def test_training_pipeline_rejects_unlabeled_test_data_before_fit(tmp_path):
    path = tmp_path / "missing_labels.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE training (value REAL, label INTEGER)")
        conn.executemany("INSERT INTO training VALUES (?, ?)", [(float(i), 0) for i in range(4)])
        conn.execute("CREATE TABLE testing (value REAL)")
        conn.executemany("INSERT INTO testing VALUES (?)", [(2.0,), (9.0,)])

    pipeline = TrainingPipeline(path)
    with pytest.raises(ValueError, match="缺少 label 列"):
        with pytest.MonkeyPatch.context() as patcher:
            run = Mock(side_effect=AssertionError("must not fit without test labels"))
            patcher.setattr(pipeline._container.algorithm_runner, "run", run)
            pipeline.run(algorithm="zscore", train_table="training", test_table="testing", persist_model=False)
    run.assert_not_called()


def test_preloaded_model_evaluation_uses_label_without_refitting(labeled_db_path):
    trained = ZScoreDetector({})
    trained.fit(np.array([[0.0], [1.0], [2.0], [3.0]]))
    trained.fit = Mock(side_effect=AssertionError("published model must never refit"))

    # An evaluation table can contain real faults; it must not be checked as
    # an all-normal training table when the model is already fitted.
    result = TrainingPipeline(labeled_db_path, train_ratio=1.0).run(
        algorithm="zscore", table="testing", preloaded_detector=trained,
        persist_model=False,
        data_contract={"model_input_scaling": {"enabled": False}},
    )
    assert result.metrics.mode == "supervised"
    assert [row["label"] for row in result.result_rows] == [1, 0, 0, 1]
    assert (result.metrics.anomaly_count, result.metrics.normal_count) == (2, 2)
    assert not result.model_info
    trained.fit.assert_not_called()
