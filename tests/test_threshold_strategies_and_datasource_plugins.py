from __future__ import annotations

from pathlib import Path

from core.plugin_runtime import PluginRuntime
from detectors.dummy_detector import DummyDetector
from detectors.registry import get_detector_config_schema
from detectors.thresholding import quantile_threshold
from evaluator.datasource_plugins.registry import DATASOURCE_REGISTRY, available_datasources


def test_sqlite_datasource_catalog_is_deduplicated_but_aliases_remain_registered():
    items = available_datasources()
    sqlite_items = [item for item in items if item.get("name") == "sqlite"]

    assert len(sqlite_items) == 1
    assert sqlite_items[0]["formats"] == ["sqlite", "db", "historydb"]
    assert DATASOURCE_REGISTRY["sqlite"] is DATASOURCE_REGISTRY["db"]
    assert DATASOURCE_REGISTRY["sqlite"] is DATASOURCE_REGISTRY["historydb"]


def test_internal_threshold_math_preserves_legacy_quantile_behaviour():
    scores = [1.0, 2.0, 3.0, 4.0]
    assert quantile_threshold(scores, ratio=0.75) == 3.0
    assert quantile_threshold(scores, ratio=0.75, method="linear") == 3.25


def test_threshold_is_not_a_top_level_plugin_family_anymore():
    catalog = PluginRuntime(Path(__file__).resolve().parents[1]).collect()
    assert "thresholds" not in catalog


def test_detector_schema_exposes_internal_threshold_strategy_not_plugin():
    schema = get_detector_config_schema("isolation_forest_detector")
    params = {item["name"]: item for item in schema["parameters"]}
    assert "threshold_plugin" not in params
    assert params["threshold_method"]["options"] == [
        "fixed", "quantile", "online_quantile", "adaptive_ewma"
    ]


def test_detector_always_produces_complete_detection_result():
    detector = DummyDetector({"threshold": 1.0})
    detector.fit([[0.0], [0.0], [0.0]])
    result = detector.detect([[0.2], [2.0]])
    assert result["anomaly_score"] == [0.2, 2.0]
    assert result["pred_label"] == [0, 1]
    assert result["is_anomaly"] == [False, True]
    assert result["thresholds"] == [1.0, 1.0]
    assert result["threshold"] == 1.0


def test_plugin_management_ui_no_longer_offers_threshold_plugin_installation():
    root = Path(__file__).resolve().parents[1]
    plugins_js = (root / "web" / "visualization" / "plugins.js").read_text(encoding="utf-8")
    plugins_html = (root / "web" / "visualization" / "plugins.html").read_text(encoding="utf-8")
    assert "{ value: 'thresholds'" not in plugins_js
    assert "BaseThresholdCalculator" not in plugins_js
    assert "阈值策略属于检测算法内部参数" in plugins_html
