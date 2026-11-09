"""Event-grounded detection charts and metrics (never infer unknown as normal)."""
from __future__ import annotations

from bisect import bisect_left
from contextlib import closing
from pathlib import Path
import math
import sqlite3
from statistics import median
from collections import defaultdict
from typing import Any

from scripts.core.fault_intervals import list_intervals
from scripts.core.multi_table_fusion import _as_seconds, _moment

MAX_ARCHIVED_INTERVALS = 500
MAX_SOURCE_TABLES = 16


def source_tables(config: dict[str, Any]) -> list[str]:
    """Resolve actual experiment sources, preserving feature order."""
    if not isinstance(config, dict):
        return []
    items = (config.get('tables') or config.get('sourceTables') or
             config.get('source_tables') or config.get('table'))
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list) or len(items) > MAX_SOURCE_TABLES:
        return []
    return list(dict.fromkeys(t for t in items if isinstance(t, str) and t.strip()))


def load_events(db_path: Path, tables: list[str]) -> list[dict[str, Any]]:
    """Read events without modifying the DB or trusting client-provided labels."""
    if len(tables) > MAX_SOURCE_TABLES:
        raise ValueError('事件来源数据表超出允许数量')
    uri = f'{db_path.resolve().as_uri()}?mode=ro'
    events = []
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        for table in tables:
            for item in list_intervals(conn, table):
                events.append({
                    'id': item['id'], 'source_table': table,
                    'start_timestamp': item['start_timestamp'],
                    'end_timestamp': item['end_timestamp'],
                    'timestamp_kind': item['timestamp_kind'],
                    'label': item['label'], 'fault_type': item['fault_type'],
                })
    return events


def _numeric_times(rows: list[dict[str, Any]]) -> tuple[str | None, list[float]]:
    if not rows:
        return None, []
    keys = []
    try:
        for row in rows:
            keys.append(_moment(row.get('timestamp')))
    except (TypeError, ValueError, OverflowError):
        return None, []
    kinds = {key[0] for key in keys}
    if len(kinds) != 1:
        return None, []
    seconds = [_as_seconds(key) for key in keys]
    if any(not math.isfinite(t) for t in seconds) or any(a > b for a, b in zip(seconds, seconds[1:])):
        return None, []
    return keys[0][0], seconds


def _mark_covered(diff: list[int], spans: list[tuple[int, int]]) -> None:
    """Count a union of half-open index ranges exactly once per source."""
    right = -1
    left = -1
    for lo, hi in sorted(spans):
        if lo >= hi:
            continue
        if left < 0:
            left, right = lo, hi
        elif lo <= right:
            right = max(right, hi)
        else:
            diff[left] += 1
            diff[right] -= 1
            left, right = lo, hi
    if left >= 0:
        diff[left] += 1
        diff[right] -= 1


def summarize_events(result: dict[str, Any], events: list[dict[str, Any]],
                     sources: list[str] | None = None) -> dict[str, Any]:
    """Event-level evaluation on FULL prediction rows, before plot decimation.

    Only fault events with their onset in the observed window AND a sampled
    observation within [start,end) are evaluable. For a multi-source model,
    the joint sample is confirmed normal only when EVERY selected source has
    a normal annotation and NO source has a fault annotation. Unannotated
    sources remain unknown, never normal. Detector decisions are not modified.
    """
    rows = result.get('rows')
    pred = result.get('pred_labels')
    if not isinstance(rows, list) or not isinstance(pred, list) or len(pred) != len(rows):
        return {'status': 'unavailable', 'reason': '缺少逐点预测标签，无法计算事件级指标'}
    if any(value not in (0, 1, '0', '1') for value in pred):
        return {'status': 'unavailable', 'reason': '预测标签包含非二值数据'}
    kind, seconds = _numeric_times(rows)
    if kind is None or not seconds:
        return {'status': 'unavailable', 'reason': '时间戳无效或无序，无法计算事件级指标'}
    predictions = [int(value) for value in pred]
    alert_indices = [i for i, value in enumerate(predictions) if value == 1]
    fault_total = 0
    eligible = 0
    detected = 0
    unseen = 0
    right_censored = 0
    latency = []
    # A joint prediction is evaluated once per time point. Summing normal
    # samples independently over each source incorrectly double-counts shared
    # normal spans and counts other-source faults as normal false alarms.
    selected_sources = list(dict.fromkeys(sources if sources is not None else
                                          [str(e.get('source_table') or '') for e in events]))
    selected_sources = [name for name in selected_sources if name]
    normal_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    fault_ranges: list[tuple[int, int]] = []
    for event in events:
        if event.get('timestamp_kind') != kind:
            continue
        try:
            start_key = _moment(event['start_timestamp'])
            end_key = _moment(event['end_timestamp'])
            if start_key[0] != kind or end_key[0] != kind:
                continue
            start = _as_seconds(start_key)
            end = _as_seconds(end_key)
        except (ValueError, TypeError, OverflowError):
            continue
        if end <= start:
            continue
        lo, hi = bisect_left(seconds, start), bisect_left(seconds, end)
        source = str(event.get('source_table') or '')
        if source not in selected_sources:
            continue
        if event.get('label') == 1:
            fault_ranges.append((lo, hi))
            fault_total += 1
            # Left-censored events cannot have a reliable onset-to-alert delay.
            if start < seconds[0] or start > seconds[-1] or lo >= hi:
                unseen += 1
                continue
            # A partial event must not be classified as a missed event: its
            # remaining lifetime lies outside the observed evaluation window.
            if end > seconds[-1]:
                unseen += 1
                right_censored += 1
                continue
            eligible += 1
            alarm_pos = bisect_left(alert_indices, lo)
            first_alert = (alert_indices[alarm_pos] if alarm_pos < len(alert_indices)
                           and alert_indices[alarm_pos] < hi else None)
            if first_alert is not None:
                detected += 1
                latency.append(max(0.0, seconds[first_alert] - start))
        elif event.get('label') == 0:
            normal_ranges[source].append((lo, hi))

    n = len(seconds)
    normal_coverage = [0] * (n + 1)
    fault_coverage = [0] * (n + 1)
    for source in selected_sources:
        _mark_covered(normal_coverage, normal_ranges[source])
    _mark_covered(fault_coverage, fault_ranges)
    normal_count = 0
    normal_alerts = 0
    fault_points = 0
    unknown_points = 0
    active_normal = active_fault = 0
    for index, prediction in enumerate(predictions):
        active_normal += normal_coverage[index]
        active_fault += fault_coverage[index]
        if active_fault:
            fault_points += 1
        elif selected_sources and active_normal == len(selected_sources):
            normal_count += 1
            normal_alerts += prediction
        else:
            unknown_points += 1
    return {
        'status': 'ok' if eligible else 'no_evaluable_events',
        'fault_events_total': fault_total,
        'fault_events_evaluable': eligible,
        'fault_events_detected': detected,
        'fault_events_not_evaluable': unseen,
        'fault_events_right_censored': right_censored,
        'event_recall': detected / eligible if eligible else None,
        'median_first_alarm_delay_seconds': median(latency) if latency else None,
        'confirmed_normal_samples': normal_count,
        'confirmed_normal_false_alarms': normal_alerts,
        'confirmed_normal_false_alarm_rate': normal_alerts / normal_count if normal_count else None,
        'joint_fault_samples': fault_points,
        'joint_unknown_samples': unknown_points,
        'joint_annotation_policy': 'all_sources_confirmed_normal_and_no_source_fault',
        'note': '故障事件按来源分别统计；联合正常样本要求全部来源已确认正常且均无故障，未知不计入误报率；左/右截断与无采样事件不计入事件召回率',
    }


def enrich_trace(trace: dict[str, Any] | None, result: dict[str, Any],
                 events: list[dict[str, Any]], sources: list[str]) -> dict[str, Any] | None:
    """Store bounded immutable event snapshots; metrics use all prediction rows."""
    if trace is None:
        return None
    trace['event_overlay'] = {
        'status': 'ok' if events else 'none',
        'interval_semantics': '[start,end)',
        'sources': sources,
        'total_intervals': len(events),
        'truncated': len(events) > MAX_ARCHIVED_INTERVALS,
        'items': events[:MAX_ARCHIVED_INTERVALS],
    }
    trace['event_metrics'] = summarize_events(result, events, sources) if events else {
        'status': 'no_annotations', 'reason': '未发现有效事件标注，无法计算事件级指标',
    }
    return trace
