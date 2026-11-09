"""Model-input normalization helpers.

The cleaning pipeline and the model input scaler have deliberately different
responsibilities:

* cleaning may fill missing values, repair outliers and smooth a series;
* model-input scaling is always applied before a detector sees numeric values.

Scaler parameters are fitted on the *training* values only and persisted with
the model so published-model evaluation can reproduce exactly the same input
scale without refitting on the evaluation set.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

VALID_METHODS = {"min_max", "zscore"}
DEFAULT_METHOD = "min_max"
_EPS = 1e-12


def normalize_method(value: Any) -> str:
    """Validate and normalize a public scaler method name."""
    method = str(value or DEFAULT_METHOD).strip().lower().replace("-", "_")
    aliases = {
        "minmax": "min_max",
        "min_max": "min_max",
        "z_score": "zscore",
        "zscore": "zscore",
    }
    method = aliases.get(method, method)
    if method not in VALID_METHODS:
        raise ValueError(
            f"不支持的模型输入归一化方法: {value!r}; allowed: min_max, zscore"
        )
    return method


def _finite_values(values: Iterable[float]) -> list[float]:
    result: list[float] = []
    for raw in values:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("模型输入包含 NaN/Inf，无法执行归一化")
        result.append(value)
    if not result:
        raise ValueError("没有可用于拟合归一化器的训练数据")
    return result


def fit_scaler(values: Iterable[float], method: str = DEFAULT_METHOD) -> dict[str, Any]:
    """Fit scaler parameters on training values only."""
    data = _finite_values(values)
    method = normalize_method(method)

    if method == "min_max":
        minimum = min(data)
        maximum = max(data)
        span = maximum - minimum
        return {
            "method": method,
            "min": minimum,
            "max": maximum,
            "span": span,
            "constant": abs(span) <= _EPS,
            "fitted_samples": len(data),
        }

    mean = sum(data) / len(data)
    variance = sum((value - mean) ** 2 for value in data) / len(data)
    std = variance ** 0.5
    return {
        "method": method,
        "mean": mean,
        "std": std,
        "constant": abs(std) <= _EPS,
        "fitted_samples": len(data),
    }


def apply_scaler(values: Iterable[float], parameters: dict[str, Any]) -> list[float]:
    """Apply previously fitted scaler parameters without refitting."""
    if not isinstance(parameters, dict):
        raise ValueError("模型输入归一化参数缺失")
    method = normalize_method(parameters.get("method"))
    data = _finite_values(values)

    if method == "min_max":
        if "min" not in parameters or "max" not in parameters:
            raise ValueError("Min-Max 归一化参数缺少 min/max")
        minimum = float(parameters["min"])
        maximum = float(parameters["max"])
        span = maximum - minimum
        if abs(span) <= _EPS:
            return [0.0 for _ in data]
        return [(value - minimum) / span for value in data]

    if "mean" not in parameters or "std" not in parameters:
        raise ValueError("Z-Score 归一化参数缺少 mean/std")
    mean = float(parameters["mean"])
    std = float(parameters["std"])
    if abs(std) <= _EPS:
        return [0.0 for _ in data]
    return [(value - mean) / std for value in data]


def scaling_summary(contract: dict[str, Any] | None) -> str:
    """Return a concise human-readable description for logs/UI."""
    if not isinstance(contract, dict) or not contract.get("enabled", False):
        return "未启用（仅兼容旧模型）"
    method = normalize_method(contract.get("method"))
    return "Min-Max" if method == "min_max" else "Z-Score"
