"""Detector decision-mode contract.

AAS gives exactly one component final anomaly-decision authority for each
registered detector instance:

``score_threshold``
    The detector produces anomaly scores and the detector-owned reusable
    threshold strategy converts them to labels.
``native``
    The algorithm itself owns the decision rule (for example sklearn's
    IsolationForest ``predict`` or a native C/C++ threshold/predict callback).
    Generic threshold strategies are disabled.
``label``
    The algorithm directly produces final labels. Scores and thresholds are
    optional and AAS never reconstructs labels from them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable


DECISION_MODE_SCORE_THRESHOLD = "score_threshold"
DECISION_MODE_NATIVE = "native"
DECISION_MODE_LABEL = "label"
SUPPORTED_DECISION_MODES = (
    DECISION_MODE_SCORE_THRESHOLD,
    DECISION_MODE_NATIVE,
    DECISION_MODE_LABEL,
)

_DECISION_MODE_ALIASES = {
    "score": DECISION_MODE_SCORE_THRESHOLD,
    "threshold": DECISION_MODE_SCORE_THRESHOLD,
    "score+threshold": DECISION_MODE_SCORE_THRESHOLD,
    "score_threshold": DECISION_MODE_SCORE_THRESHOLD,
    "framework": DECISION_MODE_SCORE_THRESHOLD,
    "native": DECISION_MODE_NATIVE,
    "algorithm": DECISION_MODE_NATIVE,
    "algorithm_native": DECISION_MODE_NATIVE,
    "label": DECISION_MODE_LABEL,
    "labels": DECISION_MODE_LABEL,
    "classification": DECISION_MODE_LABEL,
}


def normalize_decision_mode(value: Any, default: str = DECISION_MODE_SCORE_THRESHOLD) -> str:
    raw = default if value is None or str(value).strip() == "" else value
    key = str(raw).strip().lower().replace("-", "_")
    mode = _DECISION_MODE_ALIASES.get(key, key)
    if mode not in SUPPORTED_DECISION_MODES:
        raise ValueError(
            f"Unsupported decision_mode={raw!r}; expected one of "
            f"{', '.join(SUPPORTED_DECISION_MODES)}"
        )
    return mode


def _normalize_labels(values: Iterable[Any]) -> list[int]:
    return [1 if int(v) else 0 for v in values]


def _finite_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class DecisionBatch:
    """Normalized final decisions returned by a detector-owned decision path."""

    labels: list[int]
    threshold: float | None = None
    thresholds: list[float] = field(default_factory=list)

    def validate(self, expected_length: int | None = None) -> "DecisionBatch":
        self.labels = _normalize_labels(self.labels)
        self.threshold = _finite_optional_float(self.threshold)
        self.thresholds = [float(v) for v in self.thresholds]
        if not all(math.isfinite(v) for v in self.thresholds):
            raise ValueError("decision thresholds must be finite")
        if expected_length is not None:
            if len(self.labels) != expected_length:
                raise ValueError(
                    f"decision label length mismatch: {len(self.labels)} != {expected_length}"
                )
            if self.thresholds and len(self.thresholds) != expected_length:
                raise ValueError(
                    f"decision threshold trace length mismatch: "
                    f"{len(self.thresholds)} != {expected_length}"
                )
        if not self.thresholds and self.threshold is not None and expected_length is not None:
            self.thresholds = [self.threshold for _ in range(expected_length)]
        return self


def normalize_decision_output(result: Any, expected_length: int) -> DecisionBatch:
    """Accept concise plugin return shapes and normalize them to ``DecisionBatch``.

    Supported custom/native return forms:
    - ``DecisionBatch``
    - ``{"pred_label"|"labels"|"is_anomaly": ..., "threshold": ..., "thresholds": ...}``
    - ``(labels, threshold)`` or ``(labels, threshold, thresholds)``
    - a bare label sequence (primarily for ``decision_mode=label``)
    """
    if isinstance(result, DecisionBatch):
        batch = result
    elif isinstance(result, dict):
        labels = result.get("pred_label")
        if labels is None:
            labels = result.get("labels")
        if labels is None:
            labels = result.get("is_anomaly")
        if labels is None:
            raise ValueError("decision result mapping must contain pred_label/labels/is_anomaly")
        batch = DecisionBatch(
            labels=list(labels),
            threshold=result.get("threshold"),
            thresholds=list(result.get("thresholds") or []),
        )
    elif isinstance(result, tuple):
        if len(result) == 2:
            labels, threshold = result
            batch = DecisionBatch(labels=list(labels), threshold=threshold)
        elif len(result) == 3:
            labels, threshold, thresholds = result
            batch = DecisionBatch(
                labels=list(labels), threshold=threshold, thresholds=list(thresholds or [])
            )
        else:
            raise ValueError("decision tuple must be (labels, threshold[, thresholds])")
    else:
        batch = DecisionBatch(labels=list(result))
    return batch.validate(expected_length)
