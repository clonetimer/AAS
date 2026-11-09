"""预处理算子的纯函数实现（单一真相，供算子插件与历史调用复用）。"""

from __future__ import annotations

from typing import Any


def fill_missing_values(
    rows: list[dict[str, Any]], columns: list[str] | None = None
) -> list[dict[str, Any]]:
    """填充缺失值：使用前向填充 + 后向填充策略（针对 value 列）。"""
    del columns
    if not rows:
        return rows

    last_valid: float | None = None
    for row in rows:
        if "value" in row and row["value"] is not None:
            last_valid = row["value"]
        elif last_valid is not None:
            row["value"] = last_valid

    last_valid = None
    for row in reversed(rows):
        if "value" in row and row["value"] is not None:
            last_valid = row["value"]
        elif last_valid is not None:
            row["value"] = last_valid

    return rows


def repair_outliers(
    rows: list[dict[str, Any]], sigma_multiplier: float = 3.0
) -> tuple[list[dict[str, Any]], int]:
    """修复异常值：使用 σ 原则，将异常值替换为中位数。"""
    if not rows:
        return rows, 0

    values = [row["value"] for row in rows if "value" in row and row["value"] is not None]
    if not values:
        return rows, 0

    mean = sum(values) / len(values)
    std = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5

    if std == 0:
        return rows, 0

    median = sorted(values)[len(values) // 2]
    repaired_count = 0

    for row in rows:
        if "value" in row and row["value"] is not None:
            if abs(row["value"] - mean) > sigma_multiplier * std:
                row["value"] = median
                repaired_count += 1

    return rows, repaired_count


def smooth_data(
    rows: list[dict[str, Any]], window_size: int = 3
) -> list[dict[str, Any]]:
    """数据平滑：使用移动平均法。"""
    if not rows or window_size < 3:
        return rows

    values = [row["value"] for row in rows if "value" in row and row["value"] is not None]
    if len(values) < window_size:
        return rows

    smoothed: list[float] = []
    half_window = window_size // 2

    for i in range(len(values)):
        start = max(0, i - half_window)
        end = min(len(values), i + half_window + 1)
        window = values[start:end]
        smoothed.append(sum(window) / len(window))

    idx = 0
    for row in rows:
        if "value" in row and row["value"] is not None:
            row["value"] = smoothed[idx]
            idx += 1

    return rows


def normalize_data(
    rows: list[dict[str, Any]], method: str = "min_max"
) -> list[dict[str, Any]]:
    """数据归一化：支持 min_max 和 zscore 方法。"""
    if method == "none":
        return rows

    values = [row["value"] for row in rows if "value" in row and row["value"] is not None]
    if not values:
        return rows

    if method == "min_max":
        min_val = min(values)
        max_val = max(values)
        if max_val == min_val:
            return rows
        for row in rows:
            if "value" in row and row["value"] is not None:
                row["value"] = (row["value"] - min_val) / (max_val - min_val)

    elif method == "zscore":
        mean = sum(values) / len(values)
        std = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5
        if std == 0:
            return rows
        for row in rows:
            if "value" in row and row["value"] is not None:
                row["value"] = (row["value"] - mean) / std

    return rows
