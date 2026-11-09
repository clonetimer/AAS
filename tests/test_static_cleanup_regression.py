"""Regression checks for Pylint-driven maintenance without behavioral changes."""
from __future__ import annotations

import numpy as np
import pytest

from detectors.statistical_detectors import ThreeSigmaDetector
from scripts.core.data_loader import DataLoader
from scripts.core.training import TrainingPipeline


def test_statistical_detector_resets_per_feature_state_on_refit():
    """Reusing a detector after multi-feature fitting must not retain child state."""
    detector = ThreeSigmaDetector({"sigma": 3.0})
    matrix = np.asarray([[0.0, 100.0], [2.0, 102.0],
                         [4.0, 104.0], [6.0, 106.0]])
    detector.fit(matrix)
    assert detector.train_stats["feature_count"] == 2
    assert detector.score(np.asarray([[3.0, 103.0]])) == [0.0]

    detector.fit(np.asarray([[1.0], [3.0], [5.0], [7.0]]))
    assert "feature_count" not in detector.train_stats
    assert detector.train_stats["mean"] == 4.0
    assert detector.score(np.asarray([[4.0]])) == [0.0]


def test_numeric_time_range_compares_numbers_not_text(tmp_path):
    """Floating-point and numeric strings must use chronological numeric order."""
    with pytest.raises(ValueError, match="起点不能晚于终点"):
        DataLoader(tmp_path / "sample.db", time_column="timestamp",
                   time_start="20", time_end="3")
    loader = DataLoader(tmp_path / "sample.db", time_column="timestamp",
                        time_start="2", time_end="10")
    assert loader.time_start == "2"
    assert loader.time_end == "10"


def test_iso_date_time_range_rejects_reversed_boundaries(tmp_path):
    with pytest.raises(ValueError, match="起点不能晚于终点"):
        DataLoader(tmp_path / "sample.db", time_column="timestamp",
                   time_start="2026-10-20", time_end="2026-10-03")


@pytest.mark.parametrize("normal", [None, False, True, 0, 1, "0", "1",
                                     "normal", "fault", "正常", "false", "no", "unknown"])
def test_normal_label_semantics_preserved(normal):
    expected = normal is False or normal == 0 or (
        isinstance(normal, str) and normal.lower() in ("0", "normal", "正常", "false", "no")
    )
    assert TrainingPipeline._is_normal_label(normal) is expected  # pylint: disable=protected-access


def test_plugin_base_class_dispatch_keeps_lazy_supported_families():
    """Shared return path must preserve the previous plugin-type contract."""
    from core.plugin_runtime import _plugin_base_class  # pylint: disable=import-outside-toplevel

    expected = {
        "metrics": "BaseMetric", "datasources": "BaseDataSource",
        "preprocessors": "BasePreprocessOperator", "reports": "BaseReportRenderer",
        "runners": "BaseRunner", "training_strategies": "BaseTrainingStrategy",
        "fault_types": "BaseFaultType", "detectors": "BaseDetector",
    }
    for kind, classname in expected.items():
        assert _plugin_base_class(kind).__name__ == classname  # pylint: disable=protected-access
    with pytest.raises(ValueError, match="不支持的插件类型"):
        _plugin_base_class("unknown")  # pylint: disable=protected-access
