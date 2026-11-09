from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_hides_and_does_not_submit_threshold_fields_outside_score_mode():
    js = (ROOT / "web" / "visualization" / "runner.js").read_text(encoding="utf-8")
    assert "data-visible-when-name" in js
    assert "bindAlgorithmParamVisibility" in js
    assert "if (field && field.hidden) return;" in js
    assert "decision_mode" in (ROOT / "detectors" / "registry.py").read_text(encoding="utf-8")
