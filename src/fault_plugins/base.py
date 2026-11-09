"""Fault-type plugin contract used by the simulation database."""
from __future__ import annotations

from typing import Any, Callable

from src.fault_injection import FaultInjector, _normalize_timestamp, _range_bounds


class BaseFaultType(FaultInjector):  # pylint: disable=abstract-method
    """Base class for user-defined fault injection types.

    ``data`` is a ``list[dict]`` with the telemetry value in ``row['value']``.
    ``config`` always contains ``mode`` (``full`` or ``time_range``) and may
    contain ``start_time`` / ``end_time``, the common ``severity`` value, plus
    any fields declared by the plugin's ``config_schema``.
    """

    name: str = ""
    label: str = ""
    description: str = ""
    formula: str = ""
    dependencies: list[str] = []
    uses_severity: bool = True
    supports_time_range: bool = True
    config_schema: list[dict[str, Any]] = []

    def apply_to_range(
        self,
        data: list[dict[str, Any]],
        config: dict[str, Any],
        apply_fn: Callable[[dict[str, Any]], Any],
    ) -> list[dict[str, Any]]:
        """Apply ``apply_fn`` to rows selected by the standard range config."""
        return self._apply_to_range(data, config, apply_fn)

    def target_rows(
        self,
        data: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return the rows selected by ``mode`` / ``start_time`` / ``end_time``."""
        mode = config.get("mode", "full")
        if mode == "full":
            return data
        start_ts, end_ts = _range_bounds(config)
        return [
            row for row in data
            if (ts := _normalize_timestamp(row.get("timestamp"))) is not None
            and start_ts <= ts <= end_ts
        ]
