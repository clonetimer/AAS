"""Isolated multi-column CSV simulation workspace in the V2 simulation DB.

Frames are 1-based, stable CSV record order. A dataset is an immutable
snapshot; derived versions will never overwrite their parent. This module
does not write to the active/historical telemetry database.
"""
from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
import heapq
import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.server.csv_format import decompress_gzip, normalize_delimiter, resolve_csv_format
from scripts.server.csv_quality import analyze_csv_rows
from scripts.server.v2_simulation_injection import SIM_NAME, initialize


MAX_CSV_BYTES = 8_000_000
MAX_CSV_UNCOMPRESSED_BYTES = 12_000_000
MAX_CSV_FRAMES = 50_000
MAX_CSV_COLUMNS = 64
_ID = re.compile(r'[0-9a-f]{32}\Z')
_EXCLUDED_VALUES = {'frame', 'aas_frame', 'time_s', 'timestamp', 'label',
                    'fault_label', 'aas_injected_fault', 'aas_fault_events'}


def _filename(name: str) -> str:
    if (not isinstance(name, str) or not 1 <= len(name) <= 150
            or name != name.strip() or name in ('.', '..')
            or any(x in name for x in ('/', '\\', '\x00'))
            or any(ord(x) < 32 for x in name)):
        raise ValueError('只支持名称合法的本地 .csv 或 .csv.gz 文件（不接受路径）')
    lowered = name.lower()
    if lowered.endswith('.csv.gz'):
        return 'gzip'
    if lowered.endswith('.csv'):
        return 'none'
    raise ValueError('只支持名称合法的本地 .csv 或 .csv.gz 文件（不接受路径）')


def _parse(raw: bytes, filename: str, *, csv_encoding: str = 'auto',
           csv_delimiter: str = 'auto') -> tuple[list[str], list[dict[str, str]], list[str],
                                                  str | None, str | None, bool, dict]:
    compression = _filename(filename)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CSV_BYTES:
        raise ValueError('CSV 上传大小必须在 1 字节至 8 MB 以内')
    csv_bytes = decompress_gzip(raw, MAX_CSV_UNCOMPRESSED_BYTES) if compression == 'gzip' else raw
    text, resolved_encoding, resolved_delimiter = resolve_csv_format(
        csv_bytes, csv_encoding, csv_delimiter,
        max_output_bytes=MAX_CSV_UNCOMPRESSED_BYTES)
    text = normalize_delimiter(text, resolved_delimiter,
                               max_rows=MAX_CSV_FRAMES, max_bytes=MAX_CSV_UNCOMPRESSED_BYTES)
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader)
        if (not 1 <= len(header) <= MAX_CSV_COLUMNS
                or any(not name or name != name.strip() or len(name) > 128 for name in header)
                or len(set(header)) != len(header)
                or any(name.lower().startswith('aas_') for name in header)):
            raise ValueError('CSV 表头包含重复、空白、保留或超长字段名')
        rows = []
        for line in reader:
            if len(rows) >= MAX_CSV_FRAMES:
                raise ValueError('CSV 超过 50000 帧，请先按运行分文件')
            if len(line) != len(header) or any(len(v) > 2048 for v in line):
                raise ValueError(f'CSV 第 {reader.line_num} 行列数不匹配或单元格超长')
            rows.append(dict(zip(header, line)))
    except StopIteration as exc:
        raise ValueError('CSV 文件缺少表头') from exc
    except csv.Error as exc:
        raise ValueError(f'CSV 第 {reader.line_num} 行格式无效：{exc}') from exc
    if len(rows) < 2:
        raise ValueError('故障模拟至少需要 2 帧采样')
    numeric = []
    for name in header:
        if name.lower() in _EXCLUDED_VALUES:
            continue
        try:
            if all(row[name].strip() and math.isfinite(float(row[name])) for row in rows):
                numeric.append(name)
        except (ValueError, OverflowError):
            pass
    if not numeric:
        raise ValueError('CSV 没有可以完整模拟的有限数值参量列')
    time_column = next((x for x in ('time_s', 'timestamp') if x in header), None)
    label_column = next((x for x in ('label', 'fault_label') if x in header), None)
    truth = label_column is not None and all(row[label_column] in ('0', '1') for row in rows)
    format_info = {'encoding': resolved_encoding, 'delimiter': resolved_delimiter,
                   'compression': compression}
    return header, rows, numeric, time_column, label_column, truth, format_info


def inspect_csv(raw: bytes, filename: str, *, csv_encoding: str = 'auto',
                csv_delimiter: str = 'auto') -> dict:
    # Inspection is read-only; callers must separately confirm the resolved format.
    header, rows, numeric, time, label, truth, csv_format = _parse(
        raw, filename, csv_encoding=csv_encoding, csv_delimiter=csv_delimiter)
    quality = analyze_csv_rows(header, rows, time_column=time, label_column=label)
    return {'filename': filename, 'frame_count': len(rows), 'columns': header,
            'quality': quality, 'numeric_columns': numeric, 'time_column': time, 'label_column': label,
            'has_truth_labels': truth,
            'source_truth': 'labeled' if truth else 'unverified' if label else 'unlabeled',
            'csv_encoding': csv_format['encoding'], 'csv_delimiter': csv_format['delimiter'],
            'compression': csv_format['compression'], 'csv_format': csv_format,
            'quality': quality, 'format_review_required': True,
            'sample': [{'frame': i, **row} for i, row in enumerate(rows[:5], 1)]}


def _db_path(root: Path) -> Path:
    return Path(root).resolve() / 'datasets' / SIM_NAME


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS v2_csv_datasets (
        id TEXT PRIMARY KEY, parent_id TEXT REFERENCES v2_csv_datasets(id),
        filename TEXT NOT NULL, version INTEGER NOT NULL,
        columns_json TEXT NOT NULL, numeric_columns_json TEXT NOT NULL,
        time_column TEXT, label_column TEXT, has_truth_labels INTEGER NOT NULL,
        source_truth TEXT NOT NULL, root_sha256 TEXT NOT NULL,
        content_sha256 TEXT NOT NULL, frame_count INTEGER NOT NULL,
        events_json TEXT NOT NULL, created_utc TEXT NOT NULL,
        csv_format_json TEXT NOT NULL DEFAULT '{}',
        quality_json TEXT NOT NULL DEFAULT '{}',
        operations_json TEXT NOT NULL DEFAULT '[]')''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(v2_csv_datasets)')}
    if 'csv_format_json' not in columns:
        conn.execute("ALTER TABLE v2_csv_datasets ADD COLUMN csv_format_json TEXT NOT NULL DEFAULT '{}'")
    if 'quality_json' not in columns:
        conn.execute("ALTER TABLE v2_csv_datasets ADD COLUMN quality_json TEXT NOT NULL DEFAULT '{}'")
    if 'operations_json' not in columns:
        conn.execute("ALTER TABLE v2_csv_datasets ADD COLUMN operations_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute('''CREATE TABLE IF NOT EXISTS v2_csv_frames (
        dataset_id TEXT NOT NULL REFERENCES v2_csv_datasets(id) ON DELETE CASCADE,
        frame INTEGER NOT NULL CHECK(frame >= 1), row_json TEXT NOT NULL,
        PRIMARY KEY(dataset_id, frame))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_v2_csv_parent ON v2_csv_datasets(parent_id)')


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _normalize_events(events: list[dict]) -> list[dict]:
    normalized = []
    sequences = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError('故障事件账本格式无效')
        item = dict(event)
        item.setdefault('sequence', index + 1)
        item.setdefault('status', 'active')
        item.setdefault('revision', 0)
        magnitude = item.get('magnitude')
        if (not isinstance(item.get('event_id'), str)
                or not re.fullmatch(r'[0-9a-f]{16}', item['event_id'])
                or item.get('status') not in ('active', 'retracted')
                or type(item.get('sequence')) is not int or item['sequence'] < 1
                or item['sequence'] in sequences
                or type(item.get('revision')) is not int or item['revision'] < 0
                or not isinstance(item.get('column'), str) or not item['column']
                or item.get('fault_type') not in ('bias', 'drift', 'stuck', 'spike')
                or type(item.get('start_frame')) is not int or item['start_frame'] < 1
                or type(item.get('end_frame')) is not int or item['end_frame'] < item['start_frame']
                or not _is_finite_number(magnitude)):
            raise ValueError('故障事件账本包含无效状态、顺序或修订号')
        sequences.add(item['sequence'])
        normalized.append(item)
    return sorted(normalized, key=lambda item: item['sequence'])


def _header(row: sqlite3.Row) -> dict:
    item = dict(row)
    item['columns'] = json.loads(item.pop('columns_json'))
    item['numeric_columns'] = json.loads(item.pop('numeric_columns_json'))
    item['events'] = _normalize_events(json.loads(item.pop('events_json')))
    for event in item['events']:
        if (event['column'] not in item['numeric_columns']
                or event['end_frame'] > item['frame_count']):
            raise ValueError('故障事件账本引用了不存在的参量或帧区间')
    operations_json = item.pop('operations_json', '[]')
    operations = json.loads(operations_json or '[]')
    if not isinstance(operations, list) or any(not isinstance(operation, dict) for operation in operations):
        raise ValueError('版本操作审计记录格式无效')
    item['operations'] = operations
    format_json = item.pop('csv_format_json', '{}')
    item['csv_format'] = json.loads(format_json or '{}')
    item['quality'] = json.loads(item.pop('quality_json', '{}') or '{}')
    item['has_truth_labels'] = bool(item['has_truth_labels'])
    return item


def _require_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
        raise ValueError('数据集 ID 非法')
    return dataset_id


def import_csv(root: Path, raw: bytes, filename: str, *, csv_encoding: str = 'auto',
               csv_delimiter: str = 'auto', format_confirmed: bool = False) -> dict:
    # Never accept implicit/automatic format selection on the write path.
    if (format_confirmed is not True or csv_encoding in (None, 'auto')
            or csv_delimiter in (None, 'auto')):
        raise ValueError('请先检查并明确确认 CSV 编码与分隔符，再导入模拟库')
    header, rows, numeric, time, label, truth, csv_format = _parse(
        raw, filename, csv_encoding=csv_encoding, csv_delimiter=csv_delimiter)
    quality = analyze_csv_rows(header, rows, time_column=time, label_column=label)
    path = _db_path(root)
    initialize(path)
    identifier = uuid.uuid4().hex
    digest = hashlib.sha256(raw).hexdigest()
    with sqlite3.connect(path, timeout=30) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        _create_schema(conn)
        conn.execute('''INSERT INTO v2_csv_datasets(id,parent_id,filename,version,
            columns_json,numeric_columns_json,time_column,label_column,
            has_truth_labels,source_truth,root_sha256,content_sha256,
            frame_count,events_json,created_utc,csv_format_json,quality_json,operations_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, None, filename, 0, json.dumps(header,ensure_ascii=False),
             json.dumps(numeric,ensure_ascii=False), time, label, int(truth),
             'labeled' if truth else 'unverified' if label else 'unlabeled',
             digest, digest, len(rows), '[]', datetime.now(timezone.utc).isoformat(),
             json.dumps(csv_format, ensure_ascii=False, sort_keys=True),
             json.dumps(quality, ensure_ascii=False, sort_keys=True), '[]'))
        conn.executemany('INSERT INTO v2_csv_frames(dataset_id,frame,row_json) VALUES(?,?,?)',
                         ((identifier, frame, json.dumps(row,ensure_ascii=False))
                          for frame,row in enumerate(rows,1)))
        conn.commit()
    return {'filename': filename, 'frame_count': len(rows), 'columns': header,
            'numeric_columns': numeric, 'time_column': time, 'label_column': label,
            'has_truth_labels': truth,
            'source_truth': 'labeled' if truth else 'unverified' if label else 'unlabeled',
            'csv_encoding': csv_format['encoding'], 'csv_delimiter': csv_format['delimiter'],
            'compression': csv_format['compression'], 'csv_format': csv_format,
            'format_review_required': True,
            'sample': [{'frame': i, **row} for i, row in enumerate(rows[:5], 1)],
            'quality': quality,
            'id': identifier, 'parent_id': None, 'version': 0,
            'root_sha256': digest, 'content_sha256': digest}


def get_dataset(root: Path, dataset_id: str, *, include_frames: bool = True) -> dict[str, Any]:
    """Read one immutable snapshot; no DB is created by a missing read."""
    _require_id(dataset_id)
    path = _db_path(root)
    if not path.is_file():
        raise ValueError('模拟数据库尚未创建')
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        item = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?', (dataset_id,)).fetchone()
        if item is None:
            raise ValueError('工作区数据集不存在')
        result = _header(item)
        if include_frames:
            result['frames'] = [{'frame': row['frame'], 'row': json.loads(row['row_json'])}
                for row in conn.execute('SELECT frame,row_json FROM v2_csv_frames WHERE dataset_id=? ORDER BY frame',
                                        (dataset_id,))]
        return result


def list_datasets(root: Path) -> dict:
    """List original and derived snapshots for selection after page refresh."""
    path = _db_path(root)
    if not path.is_file():
        return {'items': [], 'database': SIM_NAME}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        available = conn.execute('''SELECT 1 FROM sqlite_master WHERE type='table'
                                 AND name='v2_csv_datasets' ''').fetchone()
        if not available:
            return {'items': [], 'database': SIM_NAME}
        items = []
        for row in conn.execute('SELECT * FROM v2_csv_datasets ORDER BY created_utc DESC,id DESC'):
            item = _header(row)
            events = item.pop('events')
            operations = item.pop('operations')
            item['event_count'] = len(events)
            item['active_event_count'] = sum(event.get('status', 'active') == 'active' for event in events)
            item['operation_count'] = len(operations)
            items.append(item)
    return {'items': items, 'database': SIM_NAME}


def _fault_config(source: dict, config: dict) -> dict:
    required = {'dataset_id', 'column', 'fault_type', 'start_frame', 'end_frame', 'magnitude'}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError('故障参数必须包含数据集、参量、类型、起止帧及幅值')
    kind = config['fault_type']
    if kind not in ('bias', 'drift', 'stuck', 'spike'):
        raise ValueError('CSV 模拟仅支持偏置、漂移、固定值和尖峰四种故障')
    column = config['column']
    if column not in source['numeric_columns']:
        raise ValueError('所选列不是完整的有限数值参量，禁止注入')
    start, end, magnitude = config['start_frame'], config['end_frame'], config['magnitude']
    if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= source['frame_count']):
        raise ValueError(f'注入帧区间必须为 1～{source["frame_count"]} 的整数闭区间')
    if (isinstance(magnitude, bool) or not isinstance(magnitude, (float, int))
            or not math.isfinite(magnitude) or (magnitude == 0 and kind != 'stuck')):
        raise ValueError('请输入有效的故障幅值；非固定值故障不可为 0')
    return {'dataset_id': source['id'], 'column': column, 'fault_type': kind,
            'start_frame': start, 'end_frame': end, 'magnitude': float(magnitude)}


def _simulate(source: dict, config: dict) -> tuple[list[dict], dict]:
    safe = _fault_config(source, config)
    start, end = safe['start_frame'], safe['end_frame']
    count = end - start + 1
    changed = []
    modified = 0
    for entry in source['frames']:
        frame = entry['frame']
        row = dict(entry['row'])
        before = float(row[safe['column']])
        after = before
        if start <= frame <= end:
            kind, magnitude = safe['fault_type'], safe['magnitude']
            if kind == 'bias':
                after += magnitude
            elif kind == 'drift':
                after += magnitude * (frame - start + 1) / count
            elif kind == 'stuck':
                after = magnitude
            elif (frame - start) % 8 == 0:
                after += magnitude
            if not math.isfinite(after):
                raise ValueError('注入结果包含非有限数值，请降低幅值')
            row[safe['column']] = format(after, '.15g')
            if after != before:
                modified += 1
        changed.append({'frame': frame, 'row': row,
                        'original': before, 'value': after, 'in_interval': start <= frame <= end})
    digest = hashlib.sha256(json.dumps(
        {'parent_sha256': source['content_sha256'], 'fault': safe,
         'rows': [entry['row'] for entry in changed]},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    event = {'event_id': digest[:16], 'parent_id': source['id'],
             'fault_type': safe['fault_type'], 'column': safe['column'],
             'start_frame': start, 'end_frame': end, 'magnitude': safe['magnitude'],
             'affected_frames': count, 'modified_frames': modified}
    return changed, {'preview_sha256': digest, 'event': event, 'config': safe}


def preview_fault(root: Path, config: dict) -> dict:
    """Preview non-destructively using parent-version values and 1-based frames."""
    if not isinstance(config, dict):
        raise ValueError('故障参数必须为对象')
    source = get_dataset(root, _require_id(config.get('dataset_id')))
    changed, metadata = _simulate(source, config)
    # Only the plotted points are sampled; saving always copies every frame.
    stride = max(1, (len(changed)+1198)//1199)
    points = [{'frame': item['frame'], 'original': item['original'],
               'value': item['value'], 'in_interval': item['in_interval']}
              for i, item in enumerate(changed)
              if i % stride == 0 or i == len(changed)-1]
    return {'status': 'preview', 'dataset_id': source['id'],
            'source_version': source['version'], 'total_frames': source['frame_count'],
            'fault_frames': metadata['event']['affected_frames'],
            'modified_frames': metadata['event']['modified_frames'],
            'preview_sha256': metadata['preview_sha256'], 'event': metadata['event'],
            'points': points, 'plot_sampled': len(points) < len(changed)}


def save_fault(root: Path, config: dict) -> dict:
    """Create a new full snapshot, preserving all parents and original labels."""
    if not isinstance(config, dict):
        raise ValueError('故障参数必须为对象')
    expected = config.get('preview_sha256')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('请先预览故障效果，再保存')
    trimmed = {key: value for key, value in config.items() if key != 'preview_sha256'}
    source = get_dataset(root, _require_id(trimmed.get('dataset_id')))
    changed, metadata = _simulate(source, trimmed)
    if metadata['preview_sha256'] != expected:
        raise ValueError('预览后故障参数发生变化，请重新预览')
    identifier = uuid.uuid4().hex
    version = source['version'] + 1
    events = source['events'] + [{**metadata['event'], 'sequence': len(source['events']) + 1,
                                  'status': 'active', 'revision': 0}]
    with sqlite3.connect(_db_path(root), timeout=30) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        _create_schema(conn)
        conn.execute('''INSERT INTO v2_csv_datasets(id,parent_id,filename,version,
            columns_json,numeric_columns_json,time_column,label_column,has_truth_labels,
            source_truth,root_sha256,content_sha256,frame_count,events_json,created_utc,csv_format_json,quality_json,operations_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, source['id'], source['filename'], version,
             json.dumps(source['columns'], ensure_ascii=False),
             json.dumps(source['numeric_columns'], ensure_ascii=False),
             source['time_column'], source['label_column'], int(source['has_truth_labels']),
             source['source_truth'], source['root_sha256'], metadata['preview_sha256'],
             len(changed), json.dumps(events, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(),
             json.dumps(source.get('csv_format') or {}, ensure_ascii=False, sort_keys=True),
             json.dumps(source.get('quality') or {}, ensure_ascii=False, sort_keys=True),
             json.dumps(source.get('operations') or [], ensure_ascii=False, sort_keys=True)))
        conn.executemany('INSERT INTO v2_csv_frames VALUES(?,?,?)',
                         ((identifier, entry['frame'], json.dumps(entry['row'], ensure_ascii=False))
                          for entry in changed))
        conn.commit()
    return {'status': 'saved', 'id': identifier, 'parent_id': source['id'],
            'version': version, 'frame_count': len(changed),
            'root_sha256': source['root_sha256'], 'content_sha256': metadata['preview_sha256'],
            'event': metadata['event'], 'event_count': len(events)}



def _root_id(root: Path, dataset_id: str) -> str:
    """Resolve a dataset's immutable import ancestor without loading frame rows."""
    current = _require_id(dataset_id)
    path = _db_path(root)
    if not path.is_file():
        raise ValueError('模拟数据库尚未创建')
    seen = set()
    with sqlite3.connect(path) as conn:
        while True:
            if current in seen:
                raise ValueError('数据集版本链存在循环，拒绝继续')
            seen.add(current)
            row = conn.execute('SELECT parent_id FROM v2_csv_datasets WHERE id=?', (current,)).fetchone()
            if row is None:
                raise ValueError('工作区数据集不存在')
            parent_id = row[0]
            if parent_id is None:
                return current
            current = parent_id


def list_ancestors(root: Path, dataset_id: str) -> dict:
    """Return only the strict parent chain, newest ancestor first."""
    current = _require_id(dataset_id)
    path = _db_path(root)
    if not path.is_file():
        raise ValueError('模拟数据库尚未创建')
    ancestors = []
    seen = {current}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        while True:
            row = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?', (current,)).fetchone()
            if row is None:
                raise ValueError('工作区数据集不存在')
            parent_id = row['parent_id']
            if parent_id is None:
                break
            if parent_id in seen:
                raise ValueError('数据集版本链存在循环，拒绝继续')
            seen.add(parent_id)
            parent = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?', (parent_id,)).fetchone()
            if parent is None:
                raise ValueError('数据集父版本不存在，拒绝回退')
            item = _header(parent)
            events = item.pop('events')
            operations = item.pop('operations')
            item['event_count'] = len(events)
            item['active_event_count'] = sum(event.get('status', 'active') == 'active' for event in events)
            item['operation_count'] = len(operations)
            ancestors.append(item)
            current = parent_id
    return {'dataset_id': dataset_id, 'ancestors': ancestors}


def _active_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get('status', 'active') == 'active']


def event_timeline(root: Path, dataset_id: str) -> dict:
    """Return the ordered event ledger and bounded closed-interval overlap examples."""
    source = get_dataset(root, _require_id(dataset_id), include_frames=False)
    events = _normalize_events(source['events'])
    overlap_examples: dict[str, list[dict]] = {event['event_id']: [] for event in events}
    overlap_counts: dict[str, int] = {event['event_id']: 0 for event in events}
    active = [event for event in events if event.get('status', 'active') == 'active']
    by_column: dict[str, list[dict]] = {}
    for event in active:
        by_column.setdefault(event['column'], []).append(event)

    # Exact overlap counts are O(n log n); the UI receives at most 20 pair
    # examples per event, so heavily overlapping event sets cannot explode
    # the response payload quadratically.
    max_examples = 20
    for group in by_column.values():
        starts = sorted(event['start_frame'] for event in group)
        ends = sorted(event['end_frame'] for event in group)
        for event in group:
            overlap_counts[event['event_id']] = (
                bisect_right(starts, event['end_frame'])
                - bisect_left(ends, event['start_frame']) - 1)

        ordered = sorted(group, key=lambda event: (
            event['start_frame'], event['sequence'], event['event_id']))
        active_by_id: dict[str, dict] = {}
        expiry: list[tuple[int, int, str]] = []
        for event in ordered:
            start = event['start_frame']
            while expiry and expiry[0][0] < start:
                _, _, expired_id = heapq.heappop(expiry)
                active_by_id.pop(expired_id, None)
            for previous in list(active_by_id.values())[:max_examples]:
                current_id = event['event_id']
                previous_id = previous['event_id']
                if len(overlap_examples[current_id]) < max_examples:
                    overlap_start = max(start, previous['start_frame'])
                    overlap_end = min(event['end_frame'], previous['end_frame'])
                    overlap_examples[current_id].append({
                        'event_id': previous_id, 'start_frame': overlap_start,
                        'end_frame': overlap_end,
                        'frame_count': overlap_end - overlap_start + 1})
                if len(overlap_examples[previous_id]) < max_examples:
                    overlap_start = max(start, previous['start_frame'])
                    overlap_end = min(event['end_frame'], previous['end_frame'])
                    overlap_examples[previous_id].append({
                        'event_id': current_id, 'start_frame': overlap_start,
                        'end_frame': overlap_end,
                        'frame_count': overlap_end - overlap_start + 1})
            active_by_id[event['event_id']] = event
            heapq.heappush(expiry, (event['end_frame'], event['sequence'], event['event_id']))

    result = []
    for event in events:
        item = dict(event)
        item['overlap_count'] = overlap_counts[event['event_id']]
        item['overlaps'] = sorted(overlap_examples[event['event_id']],
                                  key=lambda overlap: (overlap['start_frame'], overlap['event_id']))
        item['overlaps_truncated'] = item['overlap_count'] > len(item['overlaps'])
        result.append(item)
    return {'dataset_id': source['id'], 'version': source['version'],
            'event_count': len(result), 'active_event_count': len(active),
            'events': result, 'operations': source['operations']}


def _apply_event(rows: list[dict], event: dict, numeric_columns: list[str]) -> None:
    """Apply one previously validated event to rows in its original sequence."""
    column = event.get('column')
    kind = event.get('fault_type')
    start, end = event.get('start_frame'), event.get('end_frame')
    magnitude = event.get('magnitude')
    if (column not in numeric_columns or kind not in ('bias', 'drift', 'stuck', 'spike')
            or type(start) is not int or type(end) is not int
            or not 1 <= start <= end <= len(rows)
            or not _is_finite_number(magnitude)):
        raise ValueError('故障事件账本字段无效，拒绝重放')
    count = end - start + 1
    for frame in range(start, end + 1):
        row = rows[frame - 1]
        try:
            before = float(row[column])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError('故障事件引用了无效的源参量值，拒绝重放') from exc
        after = before
        if kind == 'bias':
            after += magnitude
        elif kind == 'drift':
            after += magnitude * (frame - start + 1) / count
        elif kind == 'stuck':
            after = float(magnitude)
        elif (frame - start) % 8 == 0:
            after += magnitude
        if not math.isfinite(after):
            raise ValueError('故障事件重放产生非有限数值，拒绝保存')
        row[column] = format(after, '.15g')


def _replay_events(root_dataset: dict, events: list[dict]) -> list[dict]:
    source_frames = root_dataset['frames']
    if (len(source_frames) != root_dataset['frame_count']
            or any(entry['frame'] != index for index, entry in enumerate(source_frames, 1))):
        raise ValueError('原始 CSV 快照帧记录不完整，拒绝重放')
    rows = [dict(entry['row']) for entry in source_frames]
    ordered = sorted(_active_events(events), key=lambda event: (int(event.get('sequence', 0)), event['event_id']))
    for event in ordered:
        _apply_event(rows, event, root_dataset['numeric_columns'])
    return [{'frame': index, 'row': row} for index, row in enumerate(rows, 1)]


def _rows_digest(frames: list[dict]) -> str:
    payload = [{'frame': item['frame'], 'row': item['row']} for item in frames]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _frame_diff(before: list[dict], after: list[dict], columns: list[str]) -> tuple[int, int]:
    changed_frames = changed_cells = 0
    if len(before) != len(after):
        raise ValueError('版本帧数不一致，无法进行逐帧差异计算')
    for expected_frame, (left, right) in enumerate(zip(before, after), 1):
        if left['frame'] != expected_frame or right['frame'] != expected_frame:
            raise ValueError('版本帧序号不一致，拒绝差异计算')
        row_changed = False
        for column in columns:
            if left['row'].get(column) != right['row'].get(column):
                changed_cells += 1
                row_changed = True
        changed_frames += int(row_changed)
    return changed_frames, changed_cells


def _preview_digest(operation: str, source: dict, target_id: str,
                    frames: list[dict], events: list[dict]) -> str:
    payload = {
        'operation': operation, 'source_id': source['id'],
        'source_content_sha256': source['content_sha256'], 'target_id': target_id,
        'result_content_sha256': _rows_digest(frames),
        'events': [{key: event.get(key) for key in ('event_id', 'status', 'revision', 'sequence')}
                   for event in events],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _save_derived_snapshot(root: Path, source: dict, frames: list[dict],
                            events: list[dict], operations: list[dict],
                            content_sha256: str) -> dict:
    identifier = uuid.uuid4().hex
    version = int(source['version']) + 1
    path = _db_path(root)
    with sqlite3.connect(path, timeout=30) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        _create_schema(conn)
        conn.execute('''INSERT INTO v2_csv_datasets(id,parent_id,filename,version,
            columns_json,numeric_columns_json,time_column,label_column,has_truth_labels,
            source_truth,root_sha256,content_sha256,frame_count,events_json,created_utc,
            csv_format_json,quality_json,operations_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, source['id'], source['filename'], version,
             json.dumps(source['columns'], ensure_ascii=False),
             json.dumps(source['numeric_columns'], ensure_ascii=False),
             source['time_column'], source['label_column'], int(source['has_truth_labels']),
             source['source_truth'], source['root_sha256'], content_sha256, len(frames),
             json.dumps(events, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat(),
             json.dumps(source.get('csv_format') or {}, ensure_ascii=False, sort_keys=True),
             json.dumps(source.get('quality') or {}, ensure_ascii=False, sort_keys=True),
             json.dumps(operations, ensure_ascii=False, sort_keys=True)))
        conn.executemany('INSERT INTO v2_csv_frames(dataset_id,frame,row_json) VALUES(?,?,?)',
                         ((identifier, item['frame'], json.dumps(item['row'], ensure_ascii=False))
                          for item in frames))
        conn.commit()
    active_count = len(_active_events(events))
    return {'status': 'saved', 'id': identifier, 'parent_id': source['id'],
            'version': version, 'frame_count': len(frames), 'event_count': len(events),
            'active_event_count': active_count, 'content_sha256': content_sha256,
            'root_sha256': source['root_sha256']}


def preview_retract_event(root: Path, dataset_id: str, event_id: str) -> dict:
    source = get_dataset(root, _require_id(dataset_id), include_frames=False)
    if not isinstance(event_id, str) or not re.fullmatch(r'[0-9a-f]{16}', event_id):
        raise ValueError('故障事件 ID 非法')
    events = _normalize_events(source['events'])
    target = next((event for event in events if event['event_id'] == event_id), None)
    if target is None:
        raise ValueError('当前版本不存在该故障事件')
    if target.get('status', 'active') != 'active':
        raise ValueError('该故障事件已经撤销，不能重复撤销')
    target['status'] = 'retracted'
    target['revision'] = int(target.get('revision', 0)) + 1
    target['status_changed_utc'] = datetime.now(timezone.utc).isoformat()
    root_dataset = get_dataset(root, _root_id(root, dataset_id))
    frames = _replay_events(root_dataset, events)
    current_frames = get_dataset(root, dataset_id)['frames']
    changed_frames, changed_cells = _frame_diff(current_frames, frames, source['columns'])
    digest = _preview_digest('event_retracted', source, event_id, frames, events)
    return {'status': 'preview', 'dataset_id': source['id'], 'event_id': event_id,
            'event': {key: target.get(key) for key in ('fault_type', 'column', 'start_frame', 'end_frame')},
            'preview_sha256': digest, 'changed_frame_count': changed_frames,
            'changed_cell_count': changed_cells, 'active_event_count': len(_active_events(events)),
            'result_content_sha256': _rows_digest(frames)}


def retract_event(root: Path, config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != {'dataset_id', 'event_id', 'preview_sha256'}:
        raise ValueError('事件撤销必须包含数据集、事件 ID 和预览摘要')
    expected = config['preview_sha256']
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('请先预览事件撤销效果')
    preview = preview_retract_event(root, config['dataset_id'], config['event_id'])
    if preview['preview_sha256'] != expected:
        raise ValueError('事件撤销预览已变化，请重新预览并确认')
    source = get_dataset(root, config['dataset_id'], include_frames=False)
    events = _normalize_events(source['events'])
    target = next(event for event in events if event['event_id'] == config['event_id'])
    target['status'] = 'retracted'
    target['revision'] = int(target.get('revision', 0)) + 1
    target['status_changed_utc'] = datetime.now(timezone.utc).isoformat()
    root_dataset = get_dataset(root, _root_id(root, config['dataset_id']))
    frames = _replay_events(root_dataset, events)
    operations = list(source.get('operations') or []) + [{
        'operation_id': uuid.uuid4().hex, 'type': 'event_retracted',
        'event_id': config['event_id'], 'source_dataset_id': source['id'],
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'result_content_sha256': _rows_digest(frames),
    }]
    return _save_derived_snapshot(root, source, frames, events, operations, _rows_digest(frames))


def _require_ancestor(root: Path, dataset_id: str, ancestor_id: str) -> dict:
    current = _require_id(dataset_id)
    ancestor_id = _require_id(ancestor_id)
    path = _db_path(root)
    seen = {current}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        while True:
            row = conn.execute('SELECT parent_id FROM v2_csv_datasets WHERE id=?', (current,)).fetchone()
            if row is None:
                raise ValueError('工作区数据集不存在')
            parent_id = row['parent_id']
            if parent_id is None:
                break
            if parent_id == ancestor_id:
                target = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?', (ancestor_id,)).fetchone()
                if target is None:
                    break
                return _header(target)
            if parent_id in seen:
                raise ValueError('数据集版本链存在循环，拒绝回退')
            seen.add(parent_id)
            current = parent_id
    raise ValueError('只能回退到当前版本的祖先版本，不能选择同级或其他分支版本')


def _rollback_state(current: dict, ancestor: dict) -> list[dict]:
    ancestor_by_id = {event['event_id']: event for event in ancestor['events']}
    merged = []
    for original in _normalize_events(current['events']):
        event = dict(original)
        target = ancestor_by_id.get(event['event_id'])
        target_status = target.get('status', 'active') if target else 'retracted'
        if event.get('status', 'active') != target_status:
            event['status'] = target_status
            event['revision'] = int(event.get('revision', 0)) + 1
            event['status_changed_utc'] = datetime.now(timezone.utc).isoformat()
        merged.append(event)
    return merged


def preview_rollback(root: Path, dataset_id: str, ancestor_id: str) -> dict:
    source = get_dataset(root, _require_id(dataset_id), include_frames=False)
    ancestor = _require_ancestor(root, source['id'], ancestor_id)
    events = _rollback_state(source, ancestor)
    target = get_dataset(root, ancestor['id'])
    current_frames = get_dataset(root, source['id'])['frames']
    changed_frames, changed_cells = _frame_diff(current_frames, target['frames'], source['columns'])
    digest = _preview_digest('version_rollback', source, ancestor['id'], target['frames'], events)
    current_by_id = {event['event_id']: event for event in source['events']}
    target_by_id = {event['event_id']: event for event in ancestor['events']}
    events_retracted = sum(current_by_id[event_id].get('status', 'active') == 'active'
                           and (event_id not in target_by_id or target_by_id[event_id].get('status', 'active') != 'active')
                           for event_id in current_by_id)
    events_reactivated = sum(current_by_id[event_id].get('status', 'active') != 'active'
                             and target_by_id[event_id].get('status', 'active') == 'active'
                             for event_id in target_by_id if event_id in current_by_id)
    return {'status': 'preview', 'dataset_id': source['id'], 'ancestor_id': ancestor['id'],
            'from_version': source['version'], 'to_version': ancestor['version'],
            'preview_sha256': digest, 'changed_frame_count': changed_frames,
            'changed_cell_count': changed_cells, 'events_retracted': events_retracted,
            'events_reactivated': events_reactivated,
            'active_event_count': len(_active_events(events)),
            'result_content_sha256': target['content_sha256']}


def rollback_to_ancestor(root: Path, config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != {'dataset_id', 'ancestor_id', 'preview_sha256'}:
        raise ValueError('版本回退必须包含当前数据集、目标祖先版本和预览摘要')
    expected = config['preview_sha256']
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('请先预览版本回退效果')
    preview = preview_rollback(root, config['dataset_id'], config['ancestor_id'])
    if preview['preview_sha256'] != expected:
        raise ValueError('版本回退预览已变化，请重新预览并确认')
    source = get_dataset(root, config['dataset_id'], include_frames=False)
    ancestor = _require_ancestor(root, source['id'], config['ancestor_id'])
    target = get_dataset(root, ancestor['id'])
    events = _rollback_state(source, ancestor)
    operations = list(source.get('operations') or []) + [{
        'operation_id': uuid.uuid4().hex, 'type': 'version_rollback',
        'ancestor_id': ancestor['id'], 'source_dataset_id': source['id'],
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'result_content_sha256': target['content_sha256'],
    }]
    return _save_derived_snapshot(root, source, target['frames'], events, operations,
                                   target['content_sha256'])


def compare_versions(root: Path, before_id: str, after_id: str, *, column: str | None = None,
                     max_points: int = 1200, max_ranges: int = 500) -> dict:
    """Compare two snapshots from one CSV import; scan all frames, bound chart output."""
    before = get_dataset(root, _require_id(before_id), include_frames=False)
    after = get_dataset(root, _require_id(after_id), include_frames=False)
    if _root_id(root, before_id) != _root_id(root, after_id):
        raise ValueError('只能比较同一原始 CSV 导入链中的两个版本')
    if before['columns'] != after['columns'] or before['frame_count'] != after['frame_count']:
        raise ValueError('版本字段或帧数不一致，无法逐帧比较')
    if type(max_points) is not int or not 2 <= max_points <= 2000:
        raise ValueError('版本比较曲线最多返回 2000 个点')
    if type(max_ranges) is not int or not 1 <= max_ranges <= 1000:
        raise ValueError('变化区间最多返回 1000 段')
    if column is None:
        column = before['numeric_columns'][0]
    if column not in before['numeric_columns']:
        raise ValueError('版本比较只支持原始 CSV 中的数值参量')
    stride = max(1, (before['frame_count'] + max_points - 2) // (max_points - 1))
    points, ranges = [], []
    changed_by_column = {name: 0 for name in before['columns']}
    changed_frame_count = changed_cell_count = 0
    range_start = range_end = None
    range_truncated = False
    seen_frames = 0
    path = _db_path(root)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA query_only=ON')
        cursor = conn.execute('''SELECT b.frame,b.row_json,a.row_json
            FROM v2_csv_frames b JOIN v2_csv_frames a ON a.frame=b.frame
            WHERE b.dataset_id=? AND a.dataset_id=? ORDER BY b.frame''', (before_id, after_id))
        for frame, before_json, after_json in cursor:
            seen_frames += 1
            if frame != seen_frames:
                raise ValueError('版本采样帧序号不连续，拒绝生成不完整比较结果')
            left, right = json.loads(before_json), json.loads(after_json)
            frame_changed = False
            for name in before['columns']:
                if left.get(name) != right.get(name):
                    changed_by_column[name] += 1
                    changed_cell_count += 1
                    frame_changed = True
            changed_frame_count += int(frame_changed)
            selected_changed = left.get(column) != right.get(column)
            if selected_changed:
                if range_start is None:
                    range_start = frame
                range_end = frame
            elif range_start is not None:
                if len(ranges) < max_ranges:
                    ranges.append({'start_frame': range_start, 'end_frame': range_end,
                                   'frame_count': range_end - range_start + 1})
                else:
                    range_truncated = True
                range_start = range_end = None
            if (frame - 1) % stride == 0 or frame == before['frame_count']:
                points.append({'frame': frame, 'before': float(left[column]), 'after': float(right[column])})
        if range_start is not None:
            if len(ranges) < max_ranges:
                ranges.append({'start_frame': range_start, 'end_frame': range_end,
                               'frame_count': range_end - range_start + 1})
            else:
                range_truncated = True
    if seen_frames != before['frame_count']:
        raise ValueError('版本采样记录不完整，拒绝生成不完整比较结果')
    before_events = {event['event_id'] for event in _active_events(before['events'])}
    after_events = {event['event_id'] for event in _active_events(after['events'])}
    ordered_after_ids = [event['event_id'] for event in _active_events(after['events'])]
    ordered_before_ids = [event['event_id'] for event in _active_events(before['events'])]
    return {'before_id': before_id, 'after_id': after_id,
            'before_version': before['version'], 'after_version': after['version'],
            'frame_count': before['frame_count'], 'column': column,
            'changed_frame_count': changed_frame_count, 'changed_cell_count': changed_cell_count,
            'changed_by_column': {key: value for key, value in changed_by_column.items() if value},
            'changed_ranges': ranges, 'changed_ranges_truncated': range_truncated,
            'points': points, 'plot_sampled': len(points) < before['frame_count'],
            'events_added': [event_id for event_id in ordered_after_ids if event_id not in before_events],
            'events_removed': [event_id for event_id in ordered_before_ids if event_id not in after_events]}

def dataset_preview(root: Path, dataset_id: str, column: str | None = None,
                    *, max_frames: int = 1200) -> dict:
    """Sample only the visualization; no CSV export or saved frame is truncated."""
    source = get_dataset(root, dataset_id)
    if column is None:
        column = source['numeric_columns'][0]
    if column not in source['numeric_columns']:
        raise ValueError('请求的参量不是此数据集的数值列')
    if type(max_frames) is not int or not 2 <= max_frames <= 2000:
        raise ValueError('曲线最多显示 2000 帧')
    stride = max(1, (source['frame_count']+max_frames-2)//(max_frames-1))
    points = []
    for i, entry in enumerate(source['frames']):
        if i % stride != 0 and i != source['frame_count']-1:
            continue
        frame = entry['frame']
        points.append({'frame': frame, 'value': float(entry['row'][column]),
                       'injected': any(e.get('status', 'active') == 'active'
                                       and e['start_frame'] <= frame <= e['end_frame'] for e in source['events']),
                       'original_label': entry['row'].get(source['label_column']) if source['label_column'] else None})
    return {**{k:v for k,v in source.items() if k != 'frames'},
            'events': [event for event in source['events'] if event.get('status', 'active') == 'active'],
            'column': column, 'points': points, 'sampled': len(points) < source['frame_count']}


def _spreadsheet_cell(value: str) -> str:
    """Preserve finite scientific numerics, but neutralize Excel formulas."""
    stripped = value.lstrip()
    if stripped and stripped[0] in '=+-@\t\r':
        try:
            if math.isfinite(float(stripped)):
                return value
        except ValueError:
            pass
        return "'" + value
    return value


def export_csv(root: Path, dataset_id: str, *, spreadsheet_safe: bool = True) -> bytes:
    """Export all rows and original columns; synthetic labels are kept separate.

    Default is safe for spreadsheet viewers. Machine-consumable raw values can
    be requested explicitly; neither variant changes any stored CSV value.
    """
    source = get_dataset(root, dataset_id)
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(['aas_frame', *source['columns'], 'aas_injected_fault', 'aas_fault_events'])
    for entry in source['frames']:
        frame, record = entry['frame'], entry['row']
        relevant = [event['event_id'] for event in source['events']
                    if event.get('status', 'active') == 'active'
                    and event['start_frame'] <= frame <= event['end_frame']]
        columns = [record[column] for column in source['columns']]
        if spreadsheet_safe:
            columns = [_spreadsheet_cell(value) for value in columns]
        writer.writerow([frame, *columns, int(bool(relevant)), ';'.join(relevant)])
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def delete_dataset(root: Path, dataset_id: str, *, confirm: bool) -> dict[str, Any]:
    """Explicitly delete one imported CSV lineage and all of its derived versions.

    Selecting any version resolves to its root and removes the full root lineage;
    unrelated imports (including identical raw bytes imported separately) remain.
    This workspace is synchronous today, so no background simulation task can
    hold a live reference while the transaction is running.
    """
    if confirm is not True:
        raise ValueError('删除模拟 CSV 必须明确确认')
    if not isinstance(dataset_id, str) or not dataset_id or len(dataset_id) > 128:
        raise ValueError('模拟数据集 ID 无效')
    path = _db_path(root)
    if not path.is_file():
        raise KeyError('模拟 CSV 数据集不存在')
    with sqlite3.connect(path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        selected = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?', (dataset_id,)).fetchone()
        if selected is None:
            raise KeyError('模拟 CSV 数据集不存在')
        root_row = selected
        visited = set()
        while root_row['parent_id']:
            if root_row['id'] in visited:
                raise ValueError('模拟数据版本链存在循环，拒绝删除')
            visited.add(root_row['id'])
            parent = conn.execute('SELECT * FROM v2_csv_datasets WHERE id=?',
                                  (root_row['parent_id'],)).fetchone()
            if parent is None:
                raise ValueError('模拟数据版本链缺少父版本，拒绝删除')
            root_row = parent
        root_id = root_row['id']
        rows = conn.execute('''WITH RECURSIVE descendants(id,version) AS (
              SELECT id,version FROM v2_csv_datasets WHERE id=?
              UNION ALL
              SELECT child.id,child.version FROM v2_csv_datasets child
              JOIN descendants parent ON child.parent_id=parent.id
            ) SELECT id,version FROM descendants ORDER BY version DESC,id DESC''',
            (root_id,)).fetchall()
        ids = [row['id'] for row in rows]
        if not ids:
            raise KeyError('模拟 CSV 数据集不存在')
        placeholders = ','.join('?' for _ in ids)
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table == 'v2_csv_datasets':
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            for fk in conn.execute(f'PRAGMA foreign_key_list({quoted})').fetchall():
                if fk[2] == 'v2_csv_datasets':
                    child_col = '"' + str(fk[3]).replace('"', '""') + '"'
                    conn.execute(f'DELETE FROM {quoted} WHERE {child_col} IN ({placeholders})', ids)
        for item in rows:
            conn.execute('DELETE FROM v2_csv_datasets WHERE id=?', (item['id'],))
    return {'deleted': True, 'root_id': root_id, 'deleted_versions': len(ids),
            'deleted_dataset_ids': ids}
