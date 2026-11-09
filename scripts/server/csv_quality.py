"""Bounded, read-only quality diagnostics for imported telemetry CSV rows.

This is advisory: it never normalizes values, assigns truth labels, or
changes the stricter checks performed by the actual database importer.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
from statistics import median
from typing import Iterable, Mapping, Sequence

MAX_ROWS = 50_000
MAX_COLUMNS = 66


def _seconds(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, OverflowError):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if date.tzinfo is not None:
                return date.timestamp()
        except (ValueError, OverflowError):
            pass
    return None


def analyze_csv_rows(header: Sequence[str], rows: Iterable[Mapping[str, str] | Sequence[str]], *,
                     time_column: str | None = None, label_column: str | None = None,
                     frame_column: str | None = None,
                     max_issues: int = 20) -> dict:
    """Return per-column counters and at most *max_issues* actual source locations.

    The first CSV data record is frame 1 / line 2. Values are neither
    repaired nor coerced in the source records. Timestamps are diagnostic;
    model eligibility is decided separately by strict import validation.
    """
    fields = list(header)
    if not 1 <= len(fields) <= MAX_COLUMNS or len(set(fields)) != len(fields):
        raise ValueError('CSV 质量检查需要 1～66 个不重复列名')
    if not isinstance(max_issues, int) or not 0 <= max_issues <= 100:
        raise ValueError('问题样本最多为 100 条')
    time_candidates = [x for x in fields if x.lower() in ('time_s', 'timestamp', 'time', 'utc_time', '采样时刻', '时间')]
    label_candidates = [x for x in fields if x.lower() in ('label', 'fault_label', 'fault_state', 'target', '标签')]
    if time_column is None:
        time_column = next((x for x in ('time_s', 'timestamp') if x in fields), None)
    if label_column is None:
        label_column = next((x for x in ('label', 'fault_label') if x in fields), None)
    if frame_column is None and time_column is None:
        frame_column = 'frame' if 'frame' in fields else None
    time_column = time_column or None
    label_column = label_column or None
    for col in (time_column, label_column, frame_column):
        if col is not None and col not in fields:
            raise ValueError(f'指定的列不存在：{col}')
    if time_column and time_column == label_column:
        raise ValueError('时间列与标签列不可相同')
    if len({x for x in (time_column,label_column,frame_column) if x}) != len([x for x in (time_column,label_column,frame_column) if x]):
        raise ValueError('时间、帧和标签列必须不同')
    measures = [x for x in fields if x not in (time_column, label_column, frame_column)]
    missing, nonnumeric, nonfinite = Counter(), Counter(), Counter()
    label_counts = {'0': 0, '1': 0, 'unknown': 0}
    issues: list[dict] = []
    issue_count = 0
    duplicate = reversals = 0
    seen_times: set[float] = set()
    previous_time: float | None = None
    measured: dict[str, int] = {x: 0 for x in measures}
    available_times: list[tuple[float, int]] = []
    row_count = 0
    previous_frame = 0
    duplicate_frames = frame_reversals = missing_frames = 0

    def report(line: int, column: str, code: str, value: object, suggestion: str):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < max_issues:
            issues.append({'line': line, 'frame': line - 1, 'column': column,
                           'code': code, 'value': str(value if value is not None else '')[:80],
                           'suggestion': suggestion})

    for frame, record in enumerate(rows, start=1):
        if frame > MAX_ROWS:
            raise ValueError('CSV 质量检查最多支持 50000 帧')
        row_count = frame
        line = frame + 1
        if isinstance(record, Mapping):
            malformed = set(record) != set(fields) or any(record.get(c) is None for c in fields)
            values = {x: record.get(x, '') for x in fields}
        else:
            malformed = len(record) != len(fields)
            values = {x: record[i] if i < len(record) else '' for i, x in enumerate(fields)}
        if malformed:
            report(line, '(整行)', 'column_count', len(record), '核对表头、引号及分隔符')
        for col in fields:
            value = values[col]
            cell = '' if value is None else str(value).strip()
            if not cell:
                missing[col] += 1
                report(line, col, 'missing', value, '保留缺测，检查数据源或选择预处理')
            if col == label_column:
                if cell in ('0', '1'):
                    label_counts[cell] += 1
                else:
                    label_counts['unknown'] += 1
                    if cell:
                        report(line, col, 'invalid_label', value, '仅 0/1 可作为二值真实标签')
                continue
            if col == frame_column:
                if not cell.isascii() or not cell.isdecimal() or cell.startswith('0'):
                    report(line,col,'invalid_frame',value,'帧号必须是从 1 开始的整数')
                else:
                    position=int(cell)
                    if position <= previous_frame:
                        duplicate_frames += int(position == previous_frame)
                        frame_reversals += int(position < previous_frame)
                        report(line,col,'duplicate_or_reversed_frame',value,'检查重复或乱序帧')
                    else:
                        missing_frames += position-previous_frame-1
                    previous_frame=position
                continue
            if col == time_column:
                t = _seconds(cell)
                if t is None:
                    if cell:
                        report(line, col, 'invalid_time', value, '指定正确时间列与格式')
                else:
                    if t in seen_times:
                        duplicate += 1
                        report(line, col, 'duplicate_time', value, '检查重复采样时间')
                    if previous_time is not None and t < previous_time:
                        reversals += 1
                        report(line, col, 'time_reversal', value, '检查采样顺序')
                    previous_time = t
                    seen_times.add(t)
                    available_times.append((t, line))
                continue
            if not cell:
                continue
            try:
                val = float(cell)
            except (ValueError, OverflowError):
                nonnumeric[col] += 1
                report(line, col, 'non_numeric', value, '选择文本列为忽略或修正非数值内容')
                continue
            if not math.isfinite(val):
                nonfinite[col] += 1
                report(line, col, 'nonfinite', value, 'NaN/Inf 不可用于建模')
            else:
                measured[col] += 1
        if label_column is None:
            label_counts['unknown'] += 1

    gaps = 0
    sample_period = None
    if len(available_times) > 1:
        ordered = sorted(available_times)
        intervals = [(b[0] - a[0], b[1], b[0]) for a, b in zip(ordered, ordered[1:]) if b[0] > a[0]]
        if intervals:
            sample_period = median([item[0] for item in intervals])
            if sample_period > 0:
                for distance, line, value in intervals:
                    if distance > sample_period * 3:
                        gaps += 1
                        report(line, time_column or '(时间)', 'large_gap', value, '检查不连续采样或分段记录')
    return {'row_count': row_count, 'column_count': len(fields),
            'coordinate_mode':'time' if time_column else 'frame' if frame_column else 'unconfirmed_row_order',
            'frame_column':frame_column,'duplicate_frames':duplicate_frames,
            'frame_reversals':frame_reversals,'missing_frames':missing_frames,
            'time_column': time_column, 'label_column': label_column,
            'time_candidates': time_candidates, 'label_candidates': label_candidates,
            'numeric_columns': [x for x in measures if measured[x] and not nonnumeric[x] and not nonfinite[x]],
            'missing_by_column': dict(missing), 'non_numeric_by_column': dict(nonnumeric),
            'nonfinite_by_column': dict(nonfinite), 'duplicate_times': duplicate,
            'time_reversals': reversals,
            'sample_period_median_seconds': sample_period,
            'gaps_gt_3x_median': gaps, 'label_counts': label_counts,
            'issue_count': issue_count, 'issues': issues,
            'issues_truncated': issue_count > len(issues)}
