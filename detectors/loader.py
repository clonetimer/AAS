"""Unified detector loading, scoring and decision service."""
from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any

import numpy as np

from detectors.registry import get_detector_descriptor, load_detector_class
from core.native_plugins import is_native_descriptor
from detectors.base import BaseDetector
from detectors.native_detector import instantiate_native_detector


def _instantiate_detector(detector_class: type, config: dict) -> Any:
    """Instantiate a detector, tolerant of config-dict and kwargs constructors."""
    sig = inspect.signature(detector_class.__init__)
    params = list(sig.parameters.keys())
    if len(params) > 1 and params[1] == "config":
        return detector_class(config=config)
    return detector_class(**config)


def load_detector(detector_spec: dict) -> Any:
    """Load and instantiate a detector from a Python/native descriptor."""
    spec = dict(detector_spec or {})
    name = str(spec.get("name") or "").strip()
    search_dir = spec.get("_search_dir")

    native = is_native_descriptor(spec)
    if not native and name:
        try:
            native = is_native_descriptor(get_detector_descriptor(name, search_dir))
        except Exception:  # pylint: disable=broad-exception-caught
            native = False
    if native:
        return instantiate_native_detector(spec, search_dir)

    config = dict(spec.get("config") or {})
    # decision_mode is part of the detector contract and may be declared at the
    # descriptor top level. Runtime config wins when explicitly supplied.
    if "decision_mode" not in config and spec.get("decision_mode") is not None:
        config["decision_mode"] = spec.get("decision_mode")
    detector_class = load_detector_class(spec)
    detector = _instantiate_detector(detector_class, config)

    declared_modes = spec.get("decision_modes")
    if declared_modes and hasattr(detector, "decision_mode"):
        normalized = {str(v).strip().lower().replace("-", "_") for v in declared_modes}
        if str(detector.decision_mode) not in normalized:
            raise ValueError(
                f"descriptor decision_modes={sorted(normalized)} does not allow "
                f"active mode {detector.decision_mode!r}"
            )
    return detector


def extract_scores(detector: Any, test_data: Any) -> list[float]:
    """Return normalized anomaly scores without inventing scores from labels."""
    if hasattr(detector, "get_scores"):
        raw = detector.get_scores(test_data)
    elif hasattr(detector, "score"):
        raw = detector.score(test_data)
    else:
        raise RuntimeError("detector does not expose anomaly scores")
    if raw is None:
        raise RuntimeError("detector returned no anomaly scores")
    return _normalize_scores(raw)


def detect_with_detector(
    detector: Any,
    test_data: Any,
    *,
    preserve_state: bool = False,
) -> dict[str, Any]:
    """Run one canonical detector decision and normalize the result.

    ``preserve_state=True`` is used by training/evaluation paths: online or
    adaptive decisions evolve realistically across the test sequence, but the
    test sequence is not allowed to contaminate the persisted trained model.
    """
    export_state = getattr(detector, "export_evaluation_state", None)
    restore_state = getattr(detector, "restore_evaluation_state", None)
    snapshot = None
    if preserve_state and callable(export_state) and callable(restore_state):
        snapshot = deepcopy(export_state())

    try:
        # BaseDetector public decision methods are framework-owned.  Call the
        # canonical implementation explicitly so a plugin cannot bypass its
        # declared decision_mode by overriding detect()/predict().
        if isinstance(detector, BaseDetector):
            result = BaseDetector.detect(detector, test_data)
            if not isinstance(result, dict):  # pragma: no cover - defensive
                raise TypeError("BaseDetector.detect() must return a mapping")
        else:
            detect = getattr(detector, "detect", None)
            if callable(detect):
                result = detect(test_data)
                if not isinstance(result, dict):
                    raise TypeError("detector.detect() must return a mapping")
            else:
                # Legacy non-BaseDetector compatibility: score + predict. New
                # plugins should inherit BaseDetector so decision_mode is explicit.
                scores = extract_scores(detector, test_data)
                predict = getattr(detector, "predict", None)
                if not callable(predict):
                    raise RuntimeError("detector has no detect()/predict() decision method")
                raw = predict(scores)
                if isinstance(raw, tuple) and len(raw) == 2:
                    labels, threshold = raw
                else:
                    labels, threshold = raw, None
                result = {
                    "anomaly_score": scores,
                    "pred_label": [1 if int(v) else 0 for v in labels],
                    "threshold": threshold,
                    "thresholds": [],
                    "decision_mode": getattr(detector, "decision_mode", "legacy"),
                }

        scores = result.get("anomaly_score")
        labels = result.get("pred_label")
        thresholds = result.get("thresholds") or []
        expected = len(test_data)
        if scores is not None:
            scores = _normalize_scores(scores)
            if len(scores) != expected:
                raise ValueError(f"anomaly_score length mismatch: {len(scores)} != {expected}")
        if labels is None:
            raise ValueError("detector decision result is missing pred_label")
        labels = [1 if int(v) else 0 for v in labels]
        if len(labels) != expected:
            raise ValueError(f"pred_label length mismatch: {len(labels)} != {expected}")
        thresholds = [float(v) for v in thresholds]
        if thresholds and len(thresholds) != expected:
            raise ValueError(f"thresholds length mismatch: {len(thresholds)} != {expected}")
        return {
            **result,
            "anomaly_score": scores,
            "pred_label": labels,
            "is_anomaly": [bool(v) for v in labels],
            "thresholds": thresholds,
            "decision_mode": str(
                result.get("decision_mode")
                or getattr(detector, "decision_mode", "legacy")
            ),
        }
    finally:
        if snapshot is not None:
            restore_state(snapshot)


def _normalize_scores(scores: Any) -> list[float]:
    if hasattr(scores, "tolist"):
        scores = scores.tolist()
    elif not isinstance(scores, list):
        scores = list(scores)
    if scores and isinstance(scores[0], (list, tuple, np.ndarray)):
        return [float(s) for sub in scores for s in sub]
    return [float(s) for s in scores]
