"""Bounded, in-memory preprocessing of a user CSV without changing any database."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import statistics
from datetime import datetime, timezone
from typing import Any

from scripts.server.csv_format import decompress_gzip, normalize_delimiter, resolve_csv_format
from scripts.server.telemetry_v2_preprocess import _transform, METHODS
from scripts.server.v2_quality import is_valid, quality_summary
from scripts.server.v2_csv_workspace import _filename, _spreadsheet_cell

MAX_BYTES = 8_000_000
MAX_OUTPUT_BYTES = 12_000_000
MAX_ROWS = 50_000
MISSING_VALUES = {'', 'na', 'n/a', 'null', 'none', 'nan'}


def _missing(value: str) -> bool:
    return value.strip().lower() in MISSING_VALUES


def _load(raw: bytes, filename: str, encoding: str = 'auto', delimiter: str = 'auto'):
    compression = _filename(filename)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('CSV 大小必须在 1 字节至 8 MB 以内')
    payload = decompress_gzip(raw, MAX_OUTPUT_BYTES) if compression == 'gzip' else raw
    text, actual_encoding, actual_delimiter = resolve_csv_format(
        payload, encoding, delimiter, max_output_bytes=MAX_OUTPUT_BYTES)
    text = normalize_delimiter(text, actual_delimiter, max_rows=MAX_ROWS,
                               max_bytes=MAX_OUTPUT_BYTES)
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError('CSV 缺少表头') from exc
    if (not 1 <= len(header) <= 64 or len(set(header)) != len(header)
            or any(not name or name != name.strip() or len(name) > 128
                   or name.lower().startswith('aas_') for name in header)):
        raise ValueError('CSV 表头含空白、重复、保留或超长字段')
    rows = []
    try:
        for line in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError('CSV 超过 50000 行，请分文件处理')
            if len(line) != len(header) or any(len(cell) > 2048 for cell in line):
                raise ValueError(f'CSV 第 {reader.line_num} 行列数不匹配或单元格过长')
            rows.append(dict(zip(header, line)))
    except csv.Error as exc:
        raise ValueError(f'CSV 第 {reader.line_num} 行格式无效') from exc
    if len(rows) < 2:
        raise ValueError('CSV 至少需要 2 行数据')
    frame_col = next((name for name in header if name.lower() == 'frame'), None)
    frames = []
    if frame_col:
        for row in rows:
            try:
                frame = int(row[frame_col])
            except (ValueError, TypeError) as exc:
                raise ValueError('frame 列必须为正整数') from exc
            if frame < 1 or str(frame) != row[frame_col].strip():
                raise ValueError('frame 列必须为从 1 开始的正整数')
            frames.append(frame)
        if frames != list(range(1, len(rows) + 1)):
            raise ValueError('frame 列须从 1 连续递增，不能重复、缺失或乱序')
    else:
        frames = list(range(1, len(rows) + 1))
    excluded = {'frame', 'time_s', 'timestamp', 'label', 'fault_label'}
    numeric = []
    for name in header:
        if name.lower() in excluded:
            continue
        values = [row[name].strip() for row in rows]
        try:
            if any(not _missing(value) and math.isfinite(float(value)) for value in values) and all(
                    _missing(value) or math.isfinite(float(value)) for value in values):
                numeric.append(name)
        except (ValueError, OverflowError):
            pass
    if not numeric:
        raise ValueError('CSV 没有可预处理的数值参量')
    return header, rows, frames, numeric, actual_encoding, actual_delimiter


def _time_keys(header, rows, frames):
    name = 'time_s' if 'time_s' in header else 'timestamp' if 'timestamp' in header else None
    if name is None:
        return frames
    keys = []
    for row in rows:
        value = row[name].strip()
        if not value:
            raise ValueError('时间列不能留空')
        try:
            key = float(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError as exc:
                raise ValueError('时间列包含无效时间') from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            key = parsed.timestamp()
        if not math.isfinite(key):
            raise ValueError('时间列包含非有限值')
        keys.append(key)
    if any(a >= b for a, b in zip(keys, keys[1:])):
        raise ValueError('时间列必须严格递增，不能重复或乱序')
    return keys


def inspect(raw: bytes, filename: str, *, encoding='auto', delimiter='auto') -> dict:
    header, rows, frames, numeric, actual_encoding, actual_delimiter = _load(
        raw, filename, encoding, delimiter)
    _time_keys(header, rows, frames)
    return {'filename': filename, 'rows': len(rows), 'columns': header,
            'numeric_columns': numeric, 'coordinate_mode': 'time' if 'time_s' in header or 'timestamp' in header else 'frame',
            'frame_origin': frames[0], 'csv_encoding': actual_encoding,
            'csv_delimiter': actual_delimiter,
            'sha256': hashlib.sha256(raw).hexdigest()}


def _params(method: str, supplied: Any, role: str) -> dict:
    if method not in METHODS:
        raise ValueError('不支持的预处理方法')
    if role not in ('train', 'test'):
        raise ValueError('请选择训练或测试用途')
    if method == 'zscore' and role != 'train':
        raise ValueError('测试数据不能重新拟合 Z-score')
    if not isinstance(supplied, dict):
        raise ValueError('预处理参数必须为对象')
    if method == 'hampel_forward':
        if set(supplied) - {'window'}:
            raise ValueError('Hampel 参数无效')
        window = supplied.get('window', 7)
        if type(window) is not int or not 3 <= window <= 101 or window % 2 != 1:
            raise ValueError('Hampel 窗口必须为 3～101 的奇数')
        return {'window': window}
    if method in ('moving_average', 'median_filter'):
        if set(supplied) - {'window'}:
            raise ValueError('窗口参数无效')
        window = supplied.get('window', 5)
        if type(window) is not int or not 2 <= window <= 501:
            raise ValueError('窗口必须为 2～501 的整数')
        return {'window': window}
    if method == 'clip':
        if set(supplied) != {'min', 'max'}:
            raise ValueError('裁剪需要上下限')
        lo, hi = supplied['min'], supplied['max']
        if type(lo) not in (int, float) or type(hi) not in (int, float) or not math.isfinite(lo) or not math.isfinite(hi) or lo >= hi:
            raise ValueError('裁剪需要有限的下限小于上限')
        return {'min': float(lo), 'max': float(hi)}
    if supplied:
        raise ValueError('当前方法不接受参数')
    return {}


def process(raw: bytes, filename: str, request: dict) -> tuple[bytes, dict]:
    if not isinstance(request, dict) or set(request) - {'column', 'method', 'params', 'role', 'encoding', 'delimiter', 'sha256'}:
        raise ValueError('预处理配置无效')
    header, records, frames, numeric, encoding, delimiter = _load(
        raw, filename, request.get('encoding', 'auto'), request.get('delimiter', 'auto'))
    if hashlib.sha256(raw).hexdigest() != request.get('sha256'):
        raise ValueError('文件已变化，请重新检查 CSV')
    column = request.get('column')
    if column not in numeric:
        raise ValueError('请选择可预处理的数值参量')
    method = request.get('method')
    role = request.get('role')
    params = _params(method, request.get('params', {}), role)
    rows = []
    time_keys = _time_keys(header, records, frames)
    for frame, record, time_key in zip(frames, records, time_keys):
        value = record[column].strip()
        rows.append({'value_num': None if _missing(value) else float(value),
                     'timestamp_raw': record.get('timestamp') or record.get('time_s') or str(frame),
                     'time_key': time_key, 'segment_id': 1, 'source_ordinal': frame})
    if method != 'hampel_forward' and any(not is_valid(row['value_num']) for row in rows):
        raise ValueError('所选参量存在缺测；请使用 Hampel + forward，或先补齐 CSV')
    points, quality, fitted = _transform(rows, method, params)
    by_frame = {row['source_ordinal']: (value, item['status'])
                for (row, value), item in zip(points, quality)}
    result = io.StringIO(newline='')
    writer = csv.writer(result, lineterminator='\r\n')
    output_column = column + '__' + method
    writer.writerow(['aas_frame', *header, output_column, 'aas_quality'])
    for frame, record in zip(frames, records):
        processed, flag = by_frame.get(frame, (None, 'not_available'))
        writer.writerow([frame, *(_spreadsheet_cell(record[name]) for name in header),
                         '' if processed is None else format(processed, '.17g'), flag])
    payload = ('\ufeff' + result.getvalue()).encode('utf-8')
    if len(payload) > 24_000_000:
        raise ValueError('预处理结果过大，请分文件处理')
    summary = quality_summary(quality)
    summary.update({'input_rows': len(rows), 'output_rows': len(points),
                    'column': column, 'method': method, 'fitted': fitted,
                    'csv_encoding': encoding, 'csv_delimiter': delimiter})
    return payload, summary
