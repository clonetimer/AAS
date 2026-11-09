from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from typing import Any


def _normalize_timestamp(value: Any) -> str | None:
    """Normalize SQLite/HTML datetime strings to a comparable ISO-like key."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.replace("T", " ", 1)


def _range_bounds(config: dict[str, Any]) -> tuple[str, str]:
    start = _normalize_timestamp(config.get("start_time"))
    end = _normalize_timestamp(config.get("end_time"))
    if start is None or end is None:
        raise ValueError("time_range mode requires start_time and end_time")
    if start > end:
        raise ValueError("start_time must not be later than end_time")
    return start, end


class FaultInjector(ABC):
    """Fault injector base class."""

    @abstractmethod
    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _apply_to_range(
        self,
        data: list[dict[str, Any]],
        config: dict[str, Any],
        apply_fn,
    ) -> list[dict[str, Any]]:
        mode = config.get("mode", "full")

        if mode == "full":
            for row in data:
                row["value"] = apply_fn(row)
            return data

        start_ts, end_ts = _range_bounds(config)

        for row in data:
            ts = _normalize_timestamp(row.get("timestamp"))
            if ts is not None and start_ts <= ts <= end_ts:
                row["value"] = apply_fn(row)

        return data


class SpikeFaultInjector(FaultInjector):
    """Spike fault: sudden value jump at specific points."""

    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        severity = config.get("severity", 1.0)

        def apply(row: dict) -> float | None:
            val = row.get("value")
            if val is None:
                return val
            spike = val + severity * abs(val) if val != 0 else severity
            return spike

        return self._apply_to_range(data, config, apply)


class DriftFaultInjector(FaultInjector):
    """Drift fault: linear or exponential drift over time."""

    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        severity = config.get("severity", 0.1)
        drift_type = config.get("drift_type", "linear")

        target_rows = self._get_target_rows(data, config)
        if not target_rows:
            return data

        n = len(target_rows)

        for i, row in enumerate(target_rows):
            val = row.get("value")
            if val is None:
                continue
            t = i / max(n - 1, 1)
            if drift_type == "linear":
                drift = severity * t * abs(val) if val != 0 else severity * t
            elif drift_type == "exponential":
                drift = severity * (math.exp(t) - 1) * abs(val) if val != 0 else severity * (math.exp(t) - 1)
            else:
                drift = severity * t * abs(val) if val != 0 else severity * t
            row["value"] = val + drift

        return data

    def _get_target_rows(self, data, config):
        mode = config.get("mode", "full")
        if mode == "full":
            return data
        start_ts, end_ts = _range_bounds(config)
        return [
            row for row in data
            if (ts := _normalize_timestamp(row.get("timestamp"))) is not None
            and start_ts <= ts <= end_ts
        ]


class BreakFaultInjector(FaultInjector):
    """Break fault: set value to NULL at specific points."""

    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        def apply(_row: dict) -> None:
            return None

        return self._apply_to_range(data, config, apply)


class PeriodicFaultInjector(FaultInjector):
    """Periodic fault: superimpose sine wave."""

    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        severity = config.get("severity", 0.5)
        frequency = config.get("frequency", 1.0)

        target_rows = self._get_target_rows(data, config)
        if not target_rows:
            return data

        n = len(target_rows)
        for i, row in enumerate(target_rows):
            val = row.get("value")
            if val is None:
                continue
            t = i / max(n - 1, 1)
            periodic = severity * math.sin(2 * math.pi * frequency * t) * abs(val) if val != 0 else severity * math.sin(2 * math.pi * frequency * t)
            row["value"] = val + periodic

        return data

    def _get_target_rows(self, data, config):
        mode = config.get("mode", "full")
        if mode == "full":
            return data
        start_ts, end_ts = _range_bounds(config)
        return [
            row for row in data
            if (ts := _normalize_timestamp(row.get("timestamp"))) is not None
            and start_ts <= ts <= end_ts
        ]


class NoiseFaultInjector(FaultInjector):
    """Noise fault: superimpose random noise."""

    def inject(self, data: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        severity = config.get("severity", 0.1)
        seed = config.get("seed")
        if seed is not None:
            random.seed(seed)

        def apply(row: dict) -> float | None:
            val = row.get("value")
            if val is None:
                return val
            noise = random.gauss(0, severity) * abs(val) if val != 0 else random.gauss(0, severity)
            return val + noise

        return self._apply_to_range(data, config, apply)


# Backwards-compatible built-in map. Runtime creation is delegated to the
# fault-type registry so custom plugins installed from the plugin center become
# immediately available to the simulation workflow.
FAULT_INJECTORS: dict[str, type[FaultInjector]] = {
    "spike": SpikeFaultInjector,
    "drift": DriftFaultInjector,
    "break": BreakFaultInjector,
    "periodic": PeriodicFaultInjector,
    "noise": NoiseFaultInjector,
}


def create_injector(fault_type: str) -> FaultInjector:
    from src.fault_plugins.registry import create_fault_injector

    return create_fault_injector(fault_type)
