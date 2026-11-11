"""Atomic import of an external wide CSV into V2 logical telemetry channels.

CSV table is a transport format only. Each measurement column becomes a V2
logical channel/segment. One source record groups the entire transaction.
"""
from __future__ import annotations

import csv
from contextlib import closing
import hashlib
import io
import json
import math
import sqlite3
import statistics
import tempfile
from collections import Counter
from typing import Callable, TextIO
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scripts.server.import_cancellation import SourceImportCancelled

from scripts.rebuild_telemetry_v2 import parse_time
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_import_context import parse_context, check_conflicts, record_context, quality_report
from scripts.server.telemetry_v2_raw_rows import ensure_raw_store
from scripts.server.telemetry_v2_import import (
    MAX_CSV_BYTES, MAX_IMPORT_ROWS, _config, _ensure_schema,
    _existing_contract, _required,
)

MAX_MEASUREMENT_COLUMNS = 64


def _prepare(payload, *, max_csv_bytes=MAX_CSV_BYTES, allow_external_filename=False):
    if isinstance(payload, dict) and payload.get('coordinate_mode') == 'frame':
        raise ValueError('帧序列请使用帧 CSV 导入入口')
    if not isinstance(payload, dict):
        raise ValueError('导入请求必须是对象')
    # _config retains all time, provenance and role checks of single-column V2 imports.
    common = _config({**payload, 'variable': '_external_wide_csv_',
                      'unit': payload.get('unit') or 'unknown'},
                     allow_external_filename=allow_external_filename)
    raw = payload.get('csv')
    if not isinstance(raw, str) or not raw or len(raw.encode('utf-8')) > max_csv_bytes:
        raise ValueError(f'遥测文本不能为空且不能超过 {max_csv_bytes // 1_000_000}MB')
    mapping = payload.get('column_metadata') or {}
    if not isinstance(mapping, dict):
        raise ValueError('column_metadata 必须是以原始列名为键的映射对象')
    reader = csv.DictReader(io.StringIO(raw.lstrip('\ufeff'), newline=''), strict=True)
    fields = reader.fieldnames or []
    if not fields or len(set(fields)) != len(fields) or any(not v or v.startswith('__') for v in fields):
        raise ValueError('CSV 表头不得重复、为空或使用保留字段')
    time_col = payload.get('time_column') if payload.get('time_column') is not None else next(
        (v for v in ('timestamp', 'time_s') if v in fields), None)
    label_col = payload.get('label_column') if payload.get('label_column') is not None else next(
        (v for v in ('label', 'fault_label') if v in fields), None)
    if not time_col or time_col not in fields:
        raise ValueError('指定的时间列不存在；请在检查列后选择正确的时间列')
    if label_col and label_col not in fields:
        raise ValueError('指定的标签列不存在')
    if time_col == label_col:
        raise ValueError('时间列与标签列不能相同')
    if not label_col:
        if common['role'] == 'train' and payload.get('all_normal_confirmed') is not True:
            raise ValueError('训练 CSV 缺少真实标签；必须确认所有样本均为正常后才能补充标签 0')
        if common['role'] == 'test' and payload.get('unlabeled_test_confirmed') is not True:
            raise ValueError('测试 CSV 缺少标签；必须确认按未知标签导入，不能计算需要真实标签的评估指标')
        if common['role'] not in ('train', 'test'):
            raise ValueError('无标签 CSV 仅支持显式选择训练或测试用途')

    columns = [f for f in fields if f not in {time_col, label_col}]
    if not 1 <= len(columns) <= MAX_MEASUREMENT_COLUMNS:
        raise ValueError('请提供 1～64 列数值遥测参量（不包括时间和标签）')
    if set(mapping) - set(columns):
        raise ValueError('参量映射存在源文件中不存在的列')
    if payload.get('require_explicit_mapping') is True and set(mapping) != set(columns):
        missing=sorted(set(columns)-set(mapping))
        raise ValueError('列映射不完整，缺少：'+', '.join(missing))
    configs = {}
    for name in columns:
        meta = mapping.get(name) or {}
        if not isinstance(meta, dict) or set(meta)-{'subsystem','device','variable','unit'}:
            raise ValueError(f'{name} 的参量映射字段不正确')
        # Automatically interpret dot-separated qualified names when provided.
        tokens = name.split('.')
        guessed_subsystem = tokens[0].upper() if len(tokens) >= 3 else common['subsystem']
        guessed_device = '.'.join(tokens[1:-1]) if len(tokens) >= 3 else common['device']
        guessed_variable = tokens[-1] if len(tokens) >= 3 else name
        configs[name] = {**common,
            'subsystem':_required(meta.get('subsystem',guessed_subsystem),'分系统'),
            'device':_required(meta.get('device',guessed_device),'部件'),
            'variable':_required(meta.get('variable',guessed_variable),'参量'),
            'unit':_required(meta.get('unit',common['unit']),'单位',max_length=32)}
    if len({tuple(cfg[key] for key in ('subsystem','device','variable')) for cfg in configs.values()}) != len(columns):
        raise ValueError('两个外部列映射到了同一个逻辑参量，拒绝歧义导入')
    data = {name: [] for name in columns}
    seen = set()
    for index, record in enumerate(reader):
        if index >= MAX_IMPORT_ROWS:raise ValueError('单次最多导入 50000 个时间采样，请分批处理')
        if None in record or any(value is None for value in record.values()):
            raise ValueError(f'CSV 第 {index+2} 行列数不一致')
        stamp = record[time_col].strip()
        domain, text_key, num_key, utc_key, type_name = parse_time(stamp)
        if domain != common['time_domain']:
            raise ValueError(f'CSV 第 {index+2} 行时间域与声明不一致')
        key = utc_key if domain == 'datetime_utc' else num_key
        if key is None or not math.isfinite(float(key)) or abs(float(key)) >= 2**53 or key in seen:
            raise ValueError(f'CSV 第 {index+2} 行时间索引无效、精度超界或重复')
        seen.add(key)
        label = record[label_col].strip() if label_col else ('0' if common['role'] == 'train' else None)
        if label is not None and (label not in ('0','1') or (common['role'] == 'train' and label != '0')):
            raise ValueError(f'CSV 第 {index+2} 行标签无效或训练数据中存在故障标签')
        # One source row is shared by all logical channels; serialize once.
        raw_row_json = json.dumps(record,ensure_ascii=False,sort_keys=True)
        for name in columns:
            cell = record[name].strip()
            if cell.lower() in ('', 'na', 'n/a', 'null', 'nan'):
                value = None  # retain the row and original time; not zero
            else:
                try: value = float(cell)
                except (TypeError,ValueError,OverflowError) as exc:
                    raise ValueError(f'CSV 第 {index+2} 行参量 {name} 不是数值') from exc
                if not math.isfinite(value):
                    raise ValueError(f'CSV 第 {index+2} 行参量 {name} 必须为有限数值，缺测请留空或使用 NA')
            data[name].append((stamp,type_name,text_key,num_key,utc_key,value,int(label) if label is not None else None,
                raw_row_json,index))
    if not seen:raise ValueError('CSV 表格不含数据行')
    for name in columns:
        data[name].sort(key=lambda x: x[4] if common['time_domain']=='datetime_utc' else x[3])
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return common,fields,configs,data,digest,len(raw.encode('utf-8'))


def _existing_contract_keys(conn, channel_id, cfg, keys):
    from scripts.server.telemetry_v2_governance import active_clause
    previous = conn.execute('''SELECT g.id, g.role, g.time_domain, c.reference,c.unit
        FROM v2_segments g LEFT JOIN v2_segment_clock c ON c.segment_id=g.id
        WHERE g.channel_id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1 AND g.role=?'''+active_clause(conn,'g'),
        (channel_id, cfg['role'])).fetchall()
    for segment in previous:
        if segment[2] != cfg['time_domain']:
            raise ValueError('该逻辑参量在相同用途下存在不同时间域，必须使用独立命名空间')
        if cfg['time_domain'] == 'numeric_unspecified' and (segment[3], segment[4]) != (cfg['time_reference'], cfg['time_unit']):
            raise ValueError('相同参量的数字时间基准或单位不一致，拒绝跨任务拼接')
    if not previous:
        return
    field = 'time_utc_us' if cfg['time_domain'] == 'datetime_utc' else 'time_sort_num'
    low, high = min(keys), max(keys)
    query = f'''SELECT s.{field} FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        WHERE s.channel_id=? AND g.role=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
        AND s.time_domain=? AND s.{field}>=? AND s.{field}<=?'''+active_clause(conn,'g')
    for (value,) in conn.execute(query,(channel_id,cfg['role'],cfg['time_domain'],low,high)):
        if value in keys:
            raise ValueError('同一参量、用途与时基下存在重叠时间戳；请先核实数据来源，不自动覆盖或复制')


def _stream_quality(stage_conn, common, missing, label_counts, row_count):
    indexed = []
    reversals = 0
    previous_ordinal = None
    for ordinal, sort_key in stage_conn.execute(
            'SELECT source_ordinal, sort_key FROM staged_rows ORDER BY sort_key'):
        indexed.append(float(sort_key))
        if previous_ordinal is not None and previous_ordinal > ordinal:
            reversals += 1
        previous_ordinal = ordinal
    scale = 1e6 if common['time_domain'] == 'datetime_utc' else {
        's': 1, 'ms': 1e3, 'us': 1e6, 'ns': 1e9}[common['time_unit']]
    steps = [(b - a) / scale for a, b in zip(indexed, indexed[1:]) if b > a]
    median_step = statistics.median(steps) if steps else None
    gaps = sum(step > 3 * median_step for step in steps) if median_step and median_step > 0 else 0
    return {
        'total_rows': row_count,
        'label_counts': {'0': label_counts[0], '1': label_counts[1], 'unknown': label_counts[None]},
        'missing_by_column': dict(missing),
        'sample_period_median_seconds': median_step,
        'gaps_gt_3x_median': gaps,
        'source_order_reversals': reversals,
        'quality_scope': 'import_data_only_no_fault_inference',
    }


def import_wide_csv_stream(db_path: Path, payload: dict, stream: TextIO, *,
                           delimiter: str = ',', commit: bool = False,
                           source_digest: str, source_size: int,
                           progress: Callable[[int, str, str], None] | None = None,
                           cancel_requested: Callable[[], bool] | None = None,
                           before_commit: Callable[[], bool] | None = None) -> dict:
    """Validate a CSV stream into a disk-backed staging DB, then atomically import it.

    The upload and decompressed CSV stay on disk. Parsed source rows are staged
    incrementally; Python memory is bounded by one CSV row, a 50k time-key set,
    and small insertion batches rather than a list of every channel sample.
    """
    if isinstance(payload,dict) and payload.get('coordinate_mode')=='frame':
        from scripts.server.telemetry_v2_frame_import import import_frame_csv_stream
        return import_frame_csv_stream(db_path,payload,stream,delimiter=delimiter,commit=commit,
            source_digest=source_digest,source_size=source_size,progress=progress,
            cancel_requested=cancel_requested,before_commit=before_commit)
    if not isinstance(payload, dict):
        raise ValueError('导入请求必须是对象')
    if delimiter not in (',', ';', '\t'):
        raise ValueError('CSV 分隔符只能选择逗号、分号或 TAB')
    if not isinstance(source_digest, str) or len(source_digest) != 64 or any(
            char not in '0123456789abcdef' for char in source_digest.lower()):
        raise ValueError('原始文件摘要非法')
    if type(source_size) is not int or source_size < 1:
        raise ValueError('原始文件大小无效')

    common = _config({**payload, 'variable': '_external_wide_csv_',
                      'unit': payload.get('unit') or 'unknown'},
                     allow_external_filename=True)
    mapping = payload.get('column_metadata') or {}
    if not isinstance(mapping, dict):
        raise ValueError('column_metadata 必须是以原始列名为键的映射对象')
    reader = csv.DictReader(stream, delimiter=delimiter, strict=True)
    fields = reader.fieldnames or []
    if not fields or len(set(fields)) != len(fields) or any(not value or value.startswith('__') for value in fields):
        raise ValueError('CSV 表头不得重复、为空或使用保留字段')
    if len(fields) > 66:
        raise ValueError('CSV 最多允许 66 列（时间、标签和 64 个参量）')
    time_col = payload.get('time_column') if payload.get('time_column') is not None else next(
        (value for value in ('timestamp', 'time_s') if value in fields), None)
    label_col = payload.get('label_column') if payload.get('label_column') is not None else next(
        (value for value in ('label', 'fault_label') if value in fields), None)
    label_col = label_col or None
    if not time_col or time_col not in fields:
        raise ValueError('指定的时间列不存在；请在检查列后选择正确的时间列')
    if label_col and label_col not in fields:
        raise ValueError('指定的标签列不存在')
    if time_col == label_col:
        raise ValueError('时间列与标签列不能相同')
    if not label_col:
        if common['role'] == 'train' and payload.get('all_normal_confirmed') is not True:
            raise ValueError('训练 CSV 缺少真实标签；必须确认所有样本均为正常后才能补充标签 0')
        if common['role'] == 'test' and payload.get('unlabeled_test_confirmed') is not True:
            raise ValueError('测试 CSV 缺少标签；必须确认按未知标签导入，不能计算需要真实标签的评估指标')
        if common['role'] not in ('train', 'test'):
            raise ValueError('无标签 CSV 仅支持显式选择训练或测试用途')

    selected = payload.get('selected_columns')
    source_columns = [value for value in fields if value not in {time_col, label_col}]
    if selected is not None:
        if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_MEASUREMENT_COLUMNS or any(
                not isinstance(value, str) or not value for value in selected) or len(set(selected)) != len(selected):
            raise ValueError('需要显式指定 1～64 个不重复的数值遥测参量')
        if set(selected) - set(source_columns):
            raise ValueError('选择参量必须来自 CSV 测量列且不能包括时间/标签')
        selected_set = set(selected)
        columns = [value for value in source_columns if value in selected_set]
    else:
        columns = source_columns
    if not 1 <= len(columns) <= MAX_MEASUREMENT_COLUMNS:
        raise ValueError('请提供 1～64 列数值遥测参量（不包括时间和标签）')
    if set(mapping) - set(columns):
        raise ValueError('参量映射存在源文件中不存在的列')
    if payload.get('require_explicit_mapping') is True and set(mapping) != set(columns):
        missing_names = sorted(set(columns) - set(mapping))
        raise ValueError('列映射不完整，缺少：' + ', '.join(missing_names))

    configs = {}
    for name in columns:
        meta = mapping.get(name) or {}
        if not isinstance(meta, dict) or set(meta) - {'subsystem', 'device', 'variable', 'unit'}:
            raise ValueError(f'{name} 的参量映射字段不正确')
        tokens = name.split('.')
        guessed_subsystem = tokens[0].upper() if len(tokens) >= 3 else common['subsystem']
        guessed_device = '.'.join(tokens[1:-1]) if len(tokens) >= 3 else common['device']
        guessed_variable = tokens[-1] if len(tokens) >= 3 else name
        configs[name] = {**common,
            'subsystem': _required(meta.get('subsystem', guessed_subsystem), '分系统'),
            'device': _required(meta.get('device', guessed_device), '部件'),
            'variable': _required(meta.get('variable', guessed_variable), '参量'),
            'unit': _required(meta.get('unit', common['unit']), '单位', max_length=32)}
    if len({tuple(cfg[key] for key in ('subsystem', 'device', 'variable')) for cfg in configs.values()}) != len(columns):
        raise ValueError('两个外部列映射到了同一个逻辑参量，拒绝歧义导入')

    row_count = 0
    missing = Counter({name: 0 for name in columns})
    label_counts = Counter({0: 0, 1: 0, None: 0})
    labels = set()
    time_keys = set()
    progress_last = -1

    def emit(value, message, stage='csv_import'):
        if progress is not None:
            progress(value, message, stage)

    with tempfile.TemporaryDirectory(prefix='aas-wide-stage-') as dirname:
        stage_path = Path(dirname) / 'stage.sqlite'
        # sqlite3.Connection.__exit__ commits/rolls back but does not close the
        # handle. On Windows this keeps stage.sqlite locked while TemporaryDirectory
        # tries to remove it. Explicitly close the staging connection on scope exit.
        with closing(sqlite3.connect(stage_path)) as stage_conn:
            stage_conn.execute('PRAGMA journal_mode=OFF')
            stage_conn.execute('PRAGMA synchronous=OFF')
            stage_conn.execute('''CREATE TABLE staged_rows (
                source_ordinal INTEGER PRIMARY KEY,
                sort_key REAL NOT NULL UNIQUE,
                timestamp_raw TEXT NOT NULL,
                timestamp_type TEXT NOT NULL,
                time_sort_text TEXT,
                time_sort_num REAL,
                time_utc_us INTEGER,
                label_int INTEGER,
                raw_row_json TEXT NOT NULL,
                values_json TEXT NOT NULL
            )''')
            batch = []
            try:
                for index, record in enumerate(reader):
                    if index >= MAX_IMPORT_ROWS:
                        raise ValueError('单次最多导入 50000 个时间采样，请分批处理')
                    if cancel_requested is not None and index % 100 == 0 and cancel_requested():
                        raise SourceImportCancelled('用户取消 CSV 导入')
                    if None in record or any(value is None for value in record.values()):
                        raise ValueError(f'CSV 第 {index + 2} 行列数不一致')
                    if any(isinstance(value, str) and '\x00' in value for value in record.values()):
                        raise ValueError(f'CSV 第 {index + 2} 行包含二进制空字符')
                    stamp = record[time_col].strip()
                    domain, text_key, num_key, utc_key, type_name = parse_time(stamp)
                    if domain != common['time_domain']:
                        raise ValueError(f'CSV 第 {index + 2} 行时间域与声明不一致')
                    sort_key = utc_key if domain == 'datetime_utc' else num_key
                    if sort_key is None or not math.isfinite(float(sort_key)) or abs(float(sort_key)) >= 2**53:
                        raise ValueError(f'CSV 第 {index + 2} 行时间索引无效、精度超界或重复')
                    if sort_key in time_keys:
                        raise ValueError(f'CSV 第 {index + 2} 行时间索引无效、精度超界或重复')
                    time_keys.add(sort_key)
                    label = record[label_col].strip() if label_col else ('0' if common['role'] == 'train' else None)
                    if label is not None and (label not in ('0', '1') or
                            (common['role'] == 'train' and label != '0')):
                        raise ValueError(f'CSV 第 {index + 2} 行标签无效或训练数据中存在故障标签')
                    if not label_col and common['role'] == 'train':
                        for original_label in ('label', 'fault_label'):
                            if original_label in record and record[original_label].strip() != '0':
                                raise ValueError('原始 CSV 已存在故障标签或无法核验的标签，不能忽略后作为全部正常训练数据')
                    label_int = int(label) if label is not None else None
                    label_counts[label_int] += 1
                    labels.add(label_int)
                    values = []
                    for name in columns:
                        cell = record[name].strip()
                        if cell.lower() in ('', 'na', 'n/a', 'null', 'nan'):
                            value = None
                            missing[name] += 1
                        else:
                            try:
                                value = float(cell)
                            except (TypeError, ValueError, OverflowError) as exc:
                                raise ValueError(f'CSV 第 {index + 2} 行参量 {name} 不是数值') from exc
                            if not math.isfinite(value):
                                raise ValueError(f'CSV 第 {index + 2} 行参量 {name} 必须为有限数值，缺测请留空或使用 NA')
                        values.append(value)
                    batch.append((index, sort_key, stamp, type_name, text_key, num_key, utc_key,
                                  label_int, json.dumps(record, ensure_ascii=False, sort_keys=True),
                                  json.dumps(values, separators=(',', ':'), allow_nan=False)))
                    row_count = index + 1
                    if len(batch) >= 500:
                        stage_conn.executemany('INSERT INTO staged_rows VALUES(?,?,?,?,?,?,?,?,?,?)', batch)
                        batch.clear()
                    if row_count % 100 == 0:
                        percent = min(70, 30 + int(40 * row_count / MAX_IMPORT_ROWS))
                        if percent != progress_last:
                            emit(percent, f'已校验 {row_count} 帧 CSV 数据')
                            progress_last = percent
                if batch:
                    stage_conn.executemany('INSERT INTO staged_rows VALUES(?,?,?,?,?,?,?,?,?,?)', batch)
                stage_conn.commit()
            except csv.Error as exc:
                raise ValueError(f'CSV 第 {reader.line_num} 行引号/字段格式错误') from exc
            if row_count == 0:
                raise ValueError('CSV 表格不含数据行')
            if cancel_requested is not None and cancel_requested():
                raise SourceImportCancelled('用户取消 CSV 导入')

            quality = _stream_quality(stage_conn, common, missing, label_counts, row_count)
            context = parse_context(payload, filename=common['filename'], labels=labels, role=common['role'])
            path = Path(db_path).resolve()
            with open_database(path):
                pass
            first = stage_conn.execute('SELECT timestamp_raw FROM staged_rows ORDER BY sort_key LIMIT 1').fetchone()[0]
            last = stage_conn.execute('SELECT timestamp_raw FROM staged_rows ORDER BY sort_key DESC LIMIT 1').fetchone()[0]
            result = {
                'status': 'imported' if commit else 'preview',
                'filename': common['filename'], 'role': common['role'],
                'label_status': ('provided' if label_col else
                                 'confirmed_normal' if common['role'] == 'train' else 'unknown'),
                'row_count': row_count, 'column_count': len(columns),
                'missing_by_column': dict(missing),
                'columns': [{'source_column': name, **{key: configs[name][key]
                    for key in ('subsystem', 'device', 'variable', 'unit')}} for name in columns],
                'sha256': source_digest, 'time_start': first, 'time_end': last,
                'quality': quality,
                'import_context': {key: value for key, value in (context or {}).items() if key != 'metadata_json'},
            }
            emit(75, 'CSV 数据校验完成，正在核对数据库映射和时间冲突')
            with sqlite3.connect(str(path), timeout=30) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE' if commit else 'BEGIN')
                _ensure_schema(conn)
                check_conflicts(conn, context, namespace=common['namespace'], model=common['model'])
                keys = time_keys
                identities = {}
                for name in columns:
                    cfg = configs[name]
                    identity = tuple(cfg[key] for key in ('namespace', 'satellite_instance', 'model',
                                                           'subsystem', 'device', 'variable'))
                    found = conn.execute('''SELECT id,unit FROM v2_channels WHERE namespace=? AND satellite_instance=?
                        AND model=? AND subsystem=? AND device=? AND variable=?''', identity).fetchone()
                    if found:
                        if found[1] != cfg['unit']:
                            raise ValueError(f'{name} 单位与已有参量不符，不允许自动覆盖')
                        _existing_contract_keys(conn, found[0], cfg, keys)
                    identities[name] = (identity, found)
                if not commit:
                    conn.rollback()
                    emit(95, 'CSV 预检完成，数据库未发生写入')
                    return result
                if before_commit is not None and not before_commit():
                    raise SourceImportCancelled('任务已取消，数据库样本尚未写入')
                source_namespace = 'upload_' + uuid4().hex
                source_name = common['filename']
                if isinstance(payload.get('source_table_name'), str) and payload['source_table_name']:
                    source_name += ' # ' + payload['source_table_name']
                source_id = conn.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
                    imported_utc,source_table_count,source_row_count) VALUES(?,?,?,?,?,?,?)''',
                    (source_namespace, source_name, source_digest, source_size,
                     datetime.now(timezone.utc).isoformat(), 1, row_count)).lastrowid
                record_context(conn, source_id, context, quality)
                ensure_raw_store(conn)
                conn.executemany('''INSERT INTO v2_source_raw_rows(source_id,source_ordinal,raw_row_json)
                    VALUES(?,?,?)''', ((source_id, ordinal, raw_json) for ordinal, raw_json in
                    stage_conn.execute('SELECT source_ordinal,raw_row_json FROM staged_rows ORDER BY source_ordinal')))
                imported = []
                inserted = 0
                for column_index, name in enumerate(columns):
                    cfg = configs[name]
                    identity, found = identities[name]
                    channel_id = found[0] if found else conn.execute('''INSERT INTO v2_channels(
                        namespace,satellite_instance,model,subsystem,device,variable,unit) VALUES(?,?,?,?,?,?,?)''',
                        (*identity, cfg['unit'])).lastrowid
                    segment_id = conn.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
                        time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                        VALUES(?,?,?,'telemetry',?,'user_explicit',?,1,?,?,?,?)''',
                        (source_id, name, channel_id, cfg['role'], cfg['time_domain'],
                         json.dumps(fields, ensure_ascii=False), row_count, first, last)).lastrowid
                    conn.execute('''INSERT INTO v2_segment_clock(segment_id,reference,unit,declared_by)
                        VALUES(?,?,?,'user_explicit')''', (segment_id, cfg['time_reference'], cfg['time_unit']))
                    cursor = stage_conn.execute('''SELECT source_ordinal,timestamp_raw,timestamp_type,time_sort_text,
                        time_sort_num,time_utc_us,label_int,values_json FROM staged_rows ORDER BY sort_key''')
                    batch_samples = []
                    while True:
                        source_rows = cursor.fetchmany(500)
                        if not source_rows:
                            break
                        for row in source_rows:
                            values = json.loads(row[7])
                            value = values[column_index]
                            batch_samples.append((segment_id, channel_id, row[0], row[1], row[2],
                                cfg['time_domain'], row[3], row[4], row[5], value,
                                json.dumps(value, allow_nan=False), row[6], json.dumps(row[6]), ''))
                        conn.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
                            time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', batch_samples)
                        inserted += len(batch_samples)
                        batch_samples.clear()
                        if inserted % 10000 < 500:
                            emit(min(98, 82 + int(16 * inserted / (row_count * len(columns)))),
                                 f'已写入 {inserted}/{row_count * len(columns)} 条参量样本')
                    imported.append({'source_column': name, 'channel_id': channel_id, 'segment_id': segment_id})
                result.update(source_id=source_id, channels=imported)
                conn.commit()
                emit(99, 'CSV 数据已提交到活动数据库')
                return result


def import_wide_csv(db_path:Path,payload:dict,*,commit:bool=False,
                    max_csv_bytes=MAX_CSV_BYTES, source_digest=None, source_size=None):
    if isinstance(payload,dict) and payload.get('coordinate_mode')=='frame':
        from scripts.server.telemetry_v2_frame_import import import_frame_csv
        return import_frame_csv(db_path,payload,commit=commit,max_csv_bytes=max_csv_bytes,
            source_digest=source_digest,source_size=source_size)
    common,fields,configs,data,digest,size = _prepare(payload, max_csv_bytes=max_csv_bytes,
                                                      allow_external_filename=source_digest is not None)
    # External binary transport supplies the digest/size of the ORIGINAL file.
    # Ordinary CSV callers retain the original behavior.
    if source_digest is not None:
        if not isinstance(source_digest, str) or len(source_digest) != 64:
            raise ValueError('原始文件摘要非法')
        digest = source_digest
        size = source_size
    quality=quality_report(data,time_domain=common['time_domain'],time_unit=common['time_unit'])
    context=parse_context(payload,filename=common['filename'],
                          labels={r[6] for r in next(iter(data.values()))},
                          role=common['role'])
    path=Path(db_path).resolve()
    with open_database(path):pass
    columns=list(data)
    result={'status':'imported' if commit else 'preview','filename':common['filename'],
        'role':common['role'],
        'label_status':('provided' if (payload.get('label_column') if payload.get('label_column') is not None else
                         next((v for v in ('label', 'fault_label') if v in fields), None))
                        else 'confirmed_normal' if common['role']=='train' else 'unknown'),
        'row_count':len(data[columns[0]]),'column_count':len(columns),
        'missing_by_column':{name:sum(row[5] is None for row in data[name]) for name in columns},
        'columns':[{'source_column':name,**{k:configs[name][k] for k in ('subsystem','device','variable','unit')}}
                   for name in columns], 'sha256':digest,
        'time_start':data[columns[0]][0][0], 'time_end':data[columns[0]][-1][0],
        'quality':quality,'import_context':{k:v for k,v in (context or {}).items() if k!='metadata_json'}}
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE' if commit else 'BEGIN')
        _ensure_schema(conn)
        check_conflicts(conn,context,namespace=common['namespace'],model=common['model'])
        identities={}
        for name in columns:
            cfg=configs[name]
            identity=tuple(cfg[k] for k in ('namespace','satellite_instance','model','subsystem','device','variable'))
            found=conn.execute('''SELECT id,unit FROM v2_channels WHERE namespace=? AND satellite_instance=?
                AND model=? AND subsystem=? AND device=? AND variable=?''',identity).fetchone()
            if found:
                if found[1] != cfg['unit']:
                    raise ValueError(f'{name} 单位与已有参量不符，不允许自动覆盖')
                _existing_contract(conn,found[0],cfg,data[name])
            identities[name]=(identity,found)
        if not commit:
            conn.rollback()
            return result
        source_namespace='upload_'+uuid4().hex
        source_name=common['filename']
        if isinstance(payload.get('source_table_name'),str) and payload['source_table_name']:
            source_name+= ' # '+payload['source_table_name']
        source_id=conn.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
            imported_utc,source_table_count,source_row_count) VALUES(?,?,?,?,?,?,?)''',
            (source_namespace,source_name,digest,size,datetime.now(timezone.utc).isoformat(),1,result['row_count'])).lastrowid
        record_context(conn,source_id,context,quality)
        ensure_raw_store(conn)
        # Each column's row tuple retains the same source ordinal and JSON.
        # Store original CSV row once per timestamp before inserting samples.
        conn.executemany('''INSERT INTO v2_source_raw_rows(source_id,source_ordinal,raw_row_json)
            VALUES(?,?,?)''', ((source_id,r[8],r[7]) for r in data[columns[0]]))
        imported=[]
        for name in columns:
            cfg=configs[name];identity,found=identities[name];rows=data[name]
            cid=found[0] if found else conn.execute('''INSERT INTO v2_channels(
                namespace,satellite_instance,model,subsystem,device,variable,unit) VALUES(?,?,?,?,?,?,?)''',
                (*identity,cfg['unit'])).lastrowid
            seg=conn.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
                time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                VALUES(?,?,?,'telemetry',?,'user_explicit',?,1,?,?,?,?)''',
                (source_id,name,cid,cfg['role'],cfg['time_domain'],json.dumps(fields,ensure_ascii=False),
                 len(rows),rows[0][0],rows[-1][0])).lastrowid
            conn.execute('''INSERT INTO v2_segment_clock(segment_id,reference,unit,declared_by) VALUES(?,?,?,?)''',
                (seg,cfg['time_reference'],cfg['time_unit'],'user_explicit'))
            conn.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
                time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ((seg,cid,r[8],r[0],r[1],cfg['time_domain'],r[2],r[3],r[4],r[5],
                  json.dumps(r[5]),r[6],json.dumps(r[6]),'') for r in rows))
            imported.append({'source_column':name,'channel_id':cid,'segment_id':seg})
        result.update(source_id=source_id,channels=imported)
        conn.commit()
        return result
