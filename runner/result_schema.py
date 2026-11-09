from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ERROR_CODES = {
    "SUCCESS",
    "PLUGIN_LOAD_ERROR",
    "DATA_LOAD_ERROR",
    "FIT_RUNTIME_ERROR",
    "SCORE_RUNTIME_ERROR",
    "PREDICT_RUNTIME_ERROR",
    "TIMEOUT",
    "OUTPUT_SCHEMA_ERROR",
    "UNKNOWN_ERROR",
}


@dataclass
class DetectorOutput:
    anomaly_score: list[float] | None
    pred_label: list[int] | None
    threshold: float | None
    thresholds: list[float] | None = None
    decision_mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def success_result(detector_meta: dict, runtime: dict, output: DetectorOutput) -> dict[str, Any]:
    return {
        "status": "success",
        "error_code": None,
        "error_message": None,
        "detector_meta": detector_meta,
        "runtime": runtime,
        "output": asdict(output),
    }


def failed_result(error_code: str, message: str, detector_meta: dict | None = None, runtime: dict | None = None) -> dict[str, Any]:
    if error_code not in ERROR_CODES:
        error_code = "UNKNOWN_ERROR"
    return {
        "status": "failed",
        "error_code": error_code,
        "error_message": message,
        "detector_meta": detector_meta or {},
        "runtime": runtime
        or {
            "train_time_ms": 0,
            "score_time_ms": 0,
            "total_time_ms": 0,
            "peak_memory_mb": None,
            "timeout": False,
        },
        "output": asdict(DetectorOutput(anomaly_score=None, pred_label=None, threshold=None)),
    }


def validate_result_schema(result: dict[str, Any], expected_length: int | None = None) -> None:
    required = {"status", "error_code", "error_message", "detector_meta", "runtime", "output"}
    missing = required - set(result.keys())
    if missing:
        raise ValueError(f"Missing result fields: {sorted(missing)}")

    output = result["output"]
    if not isinstance(output, dict):
        raise ValueError("output must be a mapping")

    scores = output.get("anomaly_score")
    labels = output.get("pred_label")
    thresholds = output.get("thresholds")
    if expected_length is not None:
        if scores is not None and len(scores) != expected_length:
            raise ValueError("anomaly_score length does not match test set length")
        if labels is not None and len(labels) != expected_length:
            raise ValueError("pred_label length does not match test set length")
        if thresholds is not None and len(thresholds) != expected_length:
            raise ValueError("thresholds length does not match test set length")
