"""Fuse single-measurement SQLite tables by exact timestamp, never by row index.

This is a transient model-input view; the source tables and their labels remain
unchanged. One sensor-specific value column is taken from each source table.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
import math
import sqlite3
from typing import Any

from scripts.server import series_catalog as catalog

MAX_SOURCES = 16
MAX_ROWS_PER_SOURCE = 200_000
MAX_GRID_ROWS = 200_000


def _moment(value: Any) -> tuple[str, float | str]:
    if value is None or isinstance(value, bool):
        raise ValueError('时间戳不能为空')
    text = str(value).strip()
    if not text:
        raise ValueError('时间戳不能为空')
    try:
        number = float(text)
    except ValueError:
        try:
            point = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(f'无法识别的时间戳：{text[:60]}') from exc
        # Naive and timezone-aware timestamps must not silently match.
        if point.tzinfo is None:
            return 'local', point.isoformat()
        return 'utc', point.timestamp()
    if not math.isfinite(number):
        raise ValueError('时间戳必须为有限数值')
    return 'numeric', number


def _checked_label(value: Any, table: str, timestamp: Any) -> int:
    if value in (0, '0', 0.0, False, 'normal', '正常'):
        return 0
    if value in (1, '1', 1.0, True, 'fault', '故障'):
        return 1
    raise ValueError(f'{table} 在 {timestamp} 的 label 无效，必须为 0 或 1')


def _as_seconds(key: tuple[str, float | str]) -> float:
    """Use a common numeric axis. Naive timestamps retain wall-clock semantics."""
    kind, value = key
    if kind == 'local':
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc).timestamp()
    return float(value)


def _nearest_unique(
    anchor_keys: list[tuple[str, float | str]],
    source: dict[tuple[str, float | str], tuple[float, int]], tolerance: float,
) -> dict[tuple[str, float | str], tuple[tuple[str, float | str], float]]:
    """One-to-one chronological nearest match; never reuse a source observation."""
    sorted_keys = sorted(source, key=_as_seconds)
    seconds = [_as_seconds(key) for key in sorted_keys]
    matches = {}
    index = 0
    # Both sides are monotone; each source observation is consumed at most once.
    # O(N+M) rather than scanning every row inside the tolerance interval.
    for anchor in anchor_keys:
        point = _as_seconds(anchor)
        while index < len(seconds) and seconds[index] < point - tolerance:
            index += 1
        if index >= len(seconds):
            break
        candidate = index
        if index + 1 < len(seconds) and (abs(seconds[index + 1] - point)
                                               < abs(seconds[index] - point)):
            candidate = index + 1
        delta = abs(seconds[candidate] - point)
        if delta <= tolerance:
            matches[anchor] = (sorted_keys[candidate], delta)
            index = candidate + 1
    return matches


def _previous_with_tolerance(
    anchors: list[tuple[str, float | str]],
    source: dict[tuple[str, float | str], tuple[float, int]], tolerance: float,
) -> dict[tuple[str, float | str], tuple[tuple[str, float | str], float]]:
    """Causal as-of join: choose only an observation not later than the anchor.

    This deliberately allows reusing a previous sample (sample-and-hold), unlike
    nearest-unique. The maximum sample age is always bounded by ``tolerance``.
    """
    ordered = sorted(source, key=_as_seconds)
    times = [_as_seconds(key) for key in ordered]
    matched = {}
    for anchor in anchors:
        t = _as_seconds(anchor)
        index = bisect_right(times, t) - 1
        if index >= 0 and 0 <= t - times[index] <= tolerance:
            matched[anchor] = (ordered[index], t - times[index])
    return matched



def _make_uniform_grid(
    series: list[dict[tuple[str, float | str], tuple[float, int]]],
    kind: str, interval: float,
    first: tuple[str, float | str] | None,
    last: tuple[str, float | str] | None,
) -> list[tuple[str, float | str]]:
    """Generate a bounded, non-persistent time axis inside all observed ranges.

    The reference epoch comes from the selected time-start (if any) or the
    first common timestamp. There is no extrapolation past a source's range.
    """
    left = max(min(_as_seconds(key) for key in values) for values in series)
    right = min(max(_as_seconds(key) for key in values) for values in series)
    if first is not None:
        left = max(left, _as_seconds(first))
    if last is not None:
        right = min(right, _as_seconds(last))
    if right < left:
        raise ValueError('所选参量没有共同时间覆盖范围，无法生成固定周期时间轴')
    count = int(math.floor((right - left) / interval + 1e-9)) + 1
    if count > MAX_GRID_ROWS:
        raise ValueError(f'固定周期时间轴超过 {MAX_GRID_ROWS} 点，请缩短时间范围或增大采样间隔')
    grid = []
    for i in range(count):
        moment = left + i * interval
        if kind == 'local':
            key: tuple[str, float | str] = ('local', datetime.fromtimestamp(
                moment, timezone.utc).replace(tzinfo=None).isoformat())
        else:
            # Preserve numeric-vs-timezone-aware type. Both are on a seconds axis.
            key = (kind, moment)
        grid.append(key)
    return grid

def fuse_sources(conn: sqlite3.Connection, tables: list[str], *,
                 time_start: str | None = None, time_end: str | None = None,
                 max_sources: int = MAX_SOURCES, tolerance_seconds: float = 0,
                 alignment_method: str = 'nearest',
                 timeline_mode: str = 'anchor',
                 grid_interval_seconds: float = 0,
                 label_alignment_method: str = 'matched',
                 virtual_rows: dict[str, list[dict[str, Any]]] | None = None,
                 virtual_signatures: dict[str, dict[str, str]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Inner join exact timestamps, aggregate sample label by OR and report coverage.

    Reject duplicates instead of arbitrary sample selection, mixing timestamp
    types, and a missing/invalid label instead of replacing it with a guess.
    """
    if isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, (int, float)) or not math.isfinite(tolerance_seconds) or not 0 <= tolerance_seconds <= 3600:
        raise ValueError('时间匹配容差必须是 0～3600 秒的有限数值（0 表示严格匹配）')
    if alignment_method not in ('nearest', 'previous'):
        raise ValueError('时间同步方式必须为 nearest 或 previous')
    if timeline_mode not in ('anchor', 'fixed'):
        raise ValueError('时间轴必须为 anchor 或 fixed')
    if label_alignment_method not in ('matched', 'previous', 'interval'):
        raise ValueError('标签映射方式必须为 matched 或 previous')
    if (isinstance(grid_interval_seconds, bool) or
            not isinstance(grid_interval_seconds, (int, float)) or
            not math.isfinite(grid_interval_seconds)):
        raise ValueError('采样间隔必须为有限数值')
    if timeline_mode == 'fixed':
        if not 0.001 <= grid_interval_seconds <= 86400:
            raise ValueError('固定时间轴采样间隔必须为 0.001～86400 秒')
        if tolerance_seconds <= 0:
            raise ValueError('固定周期时间轴必须设置大于零的时间容差')
    elif grid_interval_seconds != 0:
        raise ValueError('主表时间轴不需要采样间隔，请清空固定周期配置')
    if not isinstance(tables, list) or not 2 <= len(tables) <= max_sources:
        raise ValueError(f'多表输入要求选择 2～{max_sources} 张数据表')
    if any(not isinstance(name, str) or not name.strip() for name in tables) or len(set(tables)) != len(tables):
        raise ValueError('数据表名称不能为空或重复')
    listed = (set(virtual_rows) if virtual_rows is not None else
              {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    if virtual_rows is not None and (set(virtual_rows) != set(tables) or
                                      set(virtual_signatures or {}) != set(tables)):
        raise ValueError('逻辑参量融合的虚拟来源/身份必须与特征顺序一致')
    first = _moment(time_start) if time_start else None
    last = _moment(time_end) if time_end else None
    if first and last and (first[0] != last[0] or first[1] > last[1]):
        raise ValueError('训练时间窗口类型不一致或起点晚于终点')
    interval_lookups = []
    if label_alignment_method == 'interval':
        from scripts.core.fault_intervals import load_lookup
    series: list[dict[tuple[str, float | str], tuple[float, int]]] = []
    counts = {}
    signatures = {}
    types: set[str] = set()
    for table in tables:
        if table not in listed or table.lower().endswith('_clean_clean'):
            raise ValueError(f'数据表不存在：{table}')
        if virtual_rows is not None:
            identity = virtual_signatures[table]
            if not isinstance(identity, dict) or any(not identity.get(k) for k in catalog.IDENTITY):
                raise ValueError(f'{table} 缺少完整逻辑参量身份')
            signatures[table] = identity
            if label_alignment_method == 'interval':
                raise ValueError('逻辑参量跨表融合暂不支持事件区间标签，请使用物理表评估')
            records = [(row['timestamp'], row['value'], row['label']) for row in virtual_rows[table]]
        else:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info({quoted})')}
            required = {'timestamp', 'value'} if label_alignment_method == 'interval' else {'timestamp', 'value', 'label'}
            if not required.issubset(columns):
                raise ValueError(f'{table} 缺少必要列：{sorted(required - columns)}')
            if label_alignment_method == 'interval':
                lookup = load_lookup(conn, table)
                if lookup.kind is None:
                    raise ValueError(f'{table} 没有标注事件区间；请先录入故障与已确认正常区间')
                interval_lookups.append(lookup)
            identity = catalog.signature(conn, table)
            if identity is None:
                raise ValueError(f'{table} 含多个或不完整的型号/分系统/部件/参量，不能联合训练')
            signatures[table] = identity
            label_projection = 'label' if 'label' in columns else 'NULL AS label'
            records = conn.execute(f'SELECT timestamp, value, {label_projection} FROM {quoted} LIMIT ?',
                                   (MAX_ROWS_PER_SOURCE + 1,)).fetchall()
        if len(records) > MAX_ROWS_PER_SOURCE:
            raise ValueError(f'{table} 超过单次对齐上限 {MAX_ROWS_PER_SOURCE} 行，请先分段处理')
        by_time = {}
        for stamp, raw, label in records:
            key = _moment(stamp)
            types.add(key[0])
            # Fixed-grid matching may need a nearby sample just outside the
            # requested window. Keep at most the bounded tolerance on each side.
            if first and (key[0] != first[0] or
                    _as_seconds(key) < _as_seconds(first) -
                    (tolerance_seconds if timeline_mode == 'fixed' else 0)):
                continue
            if last and (key[0] != last[0] or
                    _as_seconds(key) > _as_seconds(last) +
                    (tolerance_seconds if timeline_mode == 'fixed' else 0)):
                continue
            if key in by_time:
                raise ValueError(f'{table} 存在重复时间戳 {stamp}，请先去重，禁止按行号拼接')
            # Event-interval truth is independent from (possibly missing) sample labels.
            fault = 0 if label_alignment_method == 'interval' else _checked_label(label, table, stamp)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value):
                by_time[key] = (value, fault)
        if not by_time:
            raise ValueError(f'{table} 在时间范围内没有有效测量数据')
        series.append(by_time)
        counts[table] = len(by_time)
    if len(types) != 1 or (first and first[0] not in types):
        raise ValueError('选中表的时间戳类型不一致，请统一采样时间和时区后训练')
    kind = next(iter(types))
    anchor_keys = (_make_uniform_grid(series, kind, grid_interval_seconds, first, last)
                   if timeline_mode == 'fixed' else
                   sorted(series[0], key=_as_seconds))
    if not anchor_keys:
        raise ValueError('时间轴没有有效采样点，请调整时间窗口或采样间隔')
    offsets_by_table: dict[str, list[float]] = {name: [] for name in tables}
    # The anchor source also needs matching on a synthetic grid.
    if tolerance_seconds:
        match_fn = _previous_with_tolerance if alignment_method == 'previous' else _nearest_unique
        source_matches = [
            match_fn(anchor_keys, values, tolerance_seconds)
            for values in (series if timeline_mode == 'fixed' else series[1:])
        ]
        if timeline_mode == 'anchor':
            source_matches.insert(0, {key: (key, 0.0) for key in anchor_keys})
    else:
        shared_exact = set.intersection(*(set(values) for values in series))
        source_matches = [{key: (key, 0.0) for key in shared_exact} for _ in series]

    # A measurement's label must never be interpolated. By default, use the
    # actual matched observation. Optional causal mapping uses the most recent
    # previous observation within the same bounded validity window.
    label_matches = (source_matches if label_alignment_method in ('matched', 'interval') else [
        _previous_with_tolerance(anchor_keys, values, tolerance_seconds)
        for values in series
    ])
    measured = [key for key in anchor_keys if all(
        key in matches and key in labels
        for matches, labels in zip(source_matches, label_matches)
    )]
    # UNKNOWN remains unknown: never invent negative labels for uncovered times.
    shared = ([key for key in measured if all(lookup.at(key) is not None
               for lookup in interval_lookups)] if label_alignment_method == 'interval' else measured)
    unknown_label_count = len(measured) - len(shared)
    if len(shared) < 4:
        raise ValueError(f'有效匹配时间戳不足（{len(shared)}），无法组成多维训练样本；请检查采样周期和容差')
    result = []
    used_sources = {table: set() for table in tables}
    for key in shared:
        samples = []
        matched_stamps = []
        for index, (table, values, matches, labels) in enumerate(zip(tables, series, source_matches, label_matches)):
            matched, offset = matches[key]
            label_key, _label_age = labels[key]
            label_value = (interval_lookups[index].at(key) if label_alignment_method == 'interval'
                           else values[label_key][1])
            samples.append((values[matched][0], label_value))
            matched_stamps.append(matched[1])
            offsets_by_table[table].append(offset)
            used_sources[table].add(matched)
        row = {'timestamp': key[1], 'value': samples[0][0],
               'label': max(sample[1] for sample in samples)}
        for index, sample in enumerate(samples, start=1):
            row[f'source_label_{index}'] = sample[1]
            row[f'source_timestamp_{index}'] = matched_stamps[index - 1]
        for index, sample in enumerate(samples[1:], start=2):
            row[f'source_{index}'] = sample[0]
        result.append(row)
    # Coverage of generated grid is different from usage of raw source rows.
    # Neither should be allowed to exceed 100% when slow sensors are held.
    coverage = {name: round(len(used_sources[name]) / count, 4)
                for name, count in counts.items()}
    offsets = {name: {'max_seconds': round(max(nums), 6),
                      'mean_seconds': round(sum(nums) / len(nums), 6)}
               for name, nums in offsets_by_table.items()}

    return result, {
        'tables': list(tables), 'source_counts': counts,
        'source_signatures': signatures,
        'aligned_count': len(shared), 'coverage': coverage,
        'timestamp_type': kind,
        'feature_columns': ['value'] + [f'source_{i}' for i in range(2, len(tables) + 1)],
        'label_policy': 'any_source_fault',
        'label_alignment_method': label_alignment_method,
        'label_mapping_policy': {'matched': 'observation_label', 'previous': 'causal_previous_label',
                                 'interval': 'explicit_intervals_unknown_excluded'}[label_alignment_method],
        'unknown_label_count': unknown_label_count,
        'annotation_fingerprints': (dict(zip(tables, [item.fingerprint for item in interval_lookups]))
                                    if label_alignment_method == 'interval' else {}),
        'interval_semantics': '[start,end)' if label_alignment_method == 'interval' else None,
        'timeline_mode': timeline_mode, 'grid_interval_seconds': grid_interval_seconds if timeline_mode == 'fixed' else 0,
        'grid_count': len(anchor_keys),
        'grid_coverage': round(len(shared) / len(anchor_keys), 4),
        'source_usage': coverage,
        'alignment_policy': ('previous_with_tolerance' if alignment_method == 'previous' else 'nearest_unique_with_tolerance') if tolerance_seconds else 'exact_timestamp_inner_join',
        'alignment_method': alignment_method,
        'tolerance_seconds': tolerance_seconds, 'offsets': offsets,
        'unmatched_anchor_count': len(anchor_keys) - len(shared),
    }
