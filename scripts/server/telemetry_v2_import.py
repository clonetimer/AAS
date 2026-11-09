"""Explicit, transactional CSV ingestion into the installed V2 telemetry database.

V2 import does not create a legacy SQLite physical table.  One upload becomes a
provenance segment; multiple uploads may belong to the same logical channel.
Only the server-chosen installed database path may be written.  No ALTER of the
original schema nor modification of legacy sources occurs.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from scripts.rebuild_telemetry_v2 import parse_time
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_import_context import parse_context,check_conflicts,record_context,quality_report

MAX_CSV_BYTES = 4_000_000
MAX_IMPORT_ROWS = 50_000
IMPORT_ROLES = frozenset({'raw', 'train', 'test', 'simulation', 'processed', 'simulated_fault'})
TIME_DOMAINS = frozenset({'datetime_utc', 'numeric_unspecified'})


def _required(value, title, *, max_length=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f'{title} 必须是 1～{max_length} 个字符')
    result = value.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in result):
        raise ValueError(f'{title} 含非法控制字符')
    return result


def _config(payload, *, allow_external_filename=False):
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是对象')
    if payload.get('coordinate_mode','time') not in ('time','frame'):
        raise ValueError('坐标模式必须为 time 或 frame')
    keys = ['namespace', 'model', 'subsystem', 'device', 'variable']
    info = {key: _required(payload.get(key), key) for key in keys}
    info['satellite_instance'] = payload.get('satellite_instance', '')
    if info['satellite_instance']:
        info['satellite_instance'] = _required(info['satellite_instance'], 'satellite_instance')
    else:
        info['satellite_instance'] = ''
    info['unit'] = _required(payload.get('unit'), 'unit', max_length=32)
    info['role'] = payload.get('role')
    if info['role'] not in IMPORT_ROLES:
        raise ValueError('导入必须显式选择 raw/train/test/simulation/processed/simulated_fault 用途')
    if payload.get('coordinate_mode', 'time') == 'frame':
        info['coordinate_mode'] = 'frame'
        info['time_domain'] = 'frame'
        info['time_reference'] = None
        info['time_unit'] = None
    else:
        info['coordinate_mode'] = 'time'
        info['time_domain'] = payload.get('time_domain')
    if info['time_domain'] not in TIME_DOMAINS | {'frame'}:
        raise ValueError('新导入仅支持带时区 UTC 时间，或具有明确时基的数值时间')
    if info['time_domain'] == 'frame':
        pass
    elif info['time_domain'] == 'numeric_unspecified':
        info['time_reference'] = _required(payload.get('time_reference'), '数值时间基准', max_length=160)
        # The source clock unit is important: a 10 ms timestamp and a 10 s timestamp must not match.
        if payload.get('time_unit') not in ('s', 'ms', 'us', 'ns'):
            raise ValueError('数值时间戳必须明确单位 s/ms/us/ns')
        info['time_unit'] = payload['time_unit']
    else:
        info['time_reference'] = 'UTC'
        info['time_unit'] = 'us'
    info['filename'] = _required(payload.get('filename', 'telemetry.csv'), 'filename', max_length=200)
    valid_name = (info['filename'].lower().endswith('.csv') or (allow_external_filename and (
        info['filename'].lower().endswith(('.csv.gz', '.sqlite', '.sqlite3', '.db')) or
        re.search(r'\.csv\([0-9]+\)\.gz$', info['filename'], re.I))))
    if not valid_name or '/' in info['filename'] or '\\' in info['filename']:
        raise ValueError('文件名必须为简单的 CSV、CSV.GZ 或 SQLite 文件名，不得包含路径')
    return info


def _parse_csv(payload, cfg):
    raw = payload.get('csv')
    if not isinstance(raw, str):
        raise ValueError('csv 必须是 UTF-8 文本')
    csv_bytes = raw.encode('utf-8')
    if not csv_bytes or len(csv_bytes) > MAX_CSV_BYTES:
        raise ValueError('CSV 必须非空且小于等于 4MB')
    # Field-size limit is global but kept bounded. CSV must be a single-measurement stream.
    try:
        # Keep the standard bounded CSV field limit; never mutate its process-wide setting.
        reader = csv.DictReader(io.StringIO(raw.lstrip('\ufeff'), newline=''), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'timestamp', 'value', 'label'} <= set(fields):
            raise ValueError('CSV 必须包含不重复的 timestamp,value,label 列')
        if any(not name or name.startswith('__') for name in fields) or len(fields) > 32:
            raise ValueError('CSV 列名非法或列数量过多')
        rows, times = [], set()
        for index, record in enumerate(reader):
            if index >= MAX_IMPORT_ROWS:
                raise ValueError('单次导入不得超过 50000 个采样，请按时间分批导入')
            if None in record or any(value is None for value in record.values()):
                raise ValueError(f'CSV 第 {index + 2} 行的列数不正确')
            timestamp = record['timestamp'].strip()
            domain, text_key, num_key, utc_key, original_type = parse_time(timestamp)
            if domain != cfg['time_domain']:
                raise ValueError(f'CSV 第 {index+2} 行时间域为 {domain}，与声明的 {cfg["time_domain"]} 不一致')
            sort_key = utc_key if domain == 'datetime_utc' else num_key
            if domain == 'numeric_unspecified':
                try:
                    original_number = Decimal(timestamp)
                except InvalidOperation as exc:
                    raise ValueError(f'CSV 第 {index+2} 行数值时间非法') from exc
                if abs(num_key) >= 2**53 or original_number != Decimal(str(num_key)):
                    raise ValueError(f'CSV 第 {index+2} 行数值时间精度超过 SQLite 浮点索引能力；请规范时间单位/格式后再导入')
            if sort_key in times:
                raise ValueError(f'CSV 第 {index+2} 行时间戳在本文件重复，不允许含糊去重')
            times.add(sort_key)
            raw_value = record['value'].strip()
            if raw_value.lower() in ('', 'na', 'n/a', 'null', 'nan'):
                # Preserve the timestamp and explicit unknown measurement. Never
                # turn missing telemetry into zero or silently remove the row.
                number = None
            else:
                try:
                    number = float(raw_value)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError(f'CSV 第 {index+2} 行数值无效') from exc
                if not math.isfinite(number):
                    raise ValueError(f'CSV 第 {index+2} 行数值必须有限（缺测请留空或用 NA）')
            label = record['label'].strip()
            if label not in ('0', '1'):
                raise ValueError(f'CSV 第 {index+2} 行标签必须为 0 或 1')
            if cfg['role'] == 'train' and label != '0':
                raise ValueError(f'CSV 第 {index+2} 行训练集含故障标签，拒绝写入')
            rows.append((timestamp, original_type, text_key, num_key, utc_key,
                         number, int(label), json.dumps(record, ensure_ascii=False, sort_keys=True), index))
    except csv.Error as exc:
        raise ValueError('CSV 格式错误：'+str(exc)) from exc
    if not rows:
        raise ValueError('CSV 没有数据行')
    rows.sort(key=lambda row: row[4] if cfg['time_domain'] == 'datetime_utc' else row[3])
    return rows, hashlib.sha256(csv_bytes).hexdigest(), len(csv_bytes), fields


def _ensure_schema(conn):
    version = conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
    if not version or str(version[0]) != '2':
        raise ValueError('不是有效的 V2 数据库，拒绝写入')
    # Non-destructive additive metadata: rebuilt V2 databases keep their version.
    from scripts.server.telemetry_v2_raw_rows import ensure_raw_store
    ensure_raw_store(conn)
    from scripts.server.telemetry_v2_coordinates import ensure_coordinate_schema
    ensure_coordinate_schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS v2_segment_clock (
        segment_id INTEGER PRIMARY KEY REFERENCES v2_segments(id),
        reference TEXT NOT NULL, unit TEXT NOT NULL, declared_by TEXT NOT NULL
    )''')


def _existing_contract(conn, channel_id, cfg, points):
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
    keys = {row[4] if cfg['time_domain'] == 'datetime_utc' else row[3] for row in points}
    low, high = min(keys), max(keys)
    query = f'''SELECT s.{field} FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        WHERE s.channel_id=? AND g.role=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
        AND s.time_domain=? AND s.{field}>=? AND s.{field}<=?'''+active_clause(conn,'g')
    for (value,) in conn.execute(query,(channel_id,cfg['role'],cfg['time_domain'],low,high)):
        if value in keys:
            raise ValueError('同一参量、用途与时基下存在重叠时间戳；请先核实数据来源，不自动覆盖或复制')


def import_csv(db_path: Path, payload: dict, *, commit=False):
    """Validate fully before a write; preview uses exactly the same conflict rules.

    Existing databases never created implicitly. A failed import rolls back all rows.
    """
    cfg = _config(payload)
    rows, digest, size, fields = _parse_csv(payload, cfg)
    quality=quality_report({'value':rows},time_domain=cfg['time_domain'],time_unit=cfg['time_unit'])
    context=parse_context(payload,filename=cfg['filename'],labels={row[6] for row in rows},role=cfg['role'])
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError('V2 数据库尚未安装，不允许创建空库覆盖业务数据')
    # sqlite connects after V2 header inspection. No browser-controlled database location.
    with open_database(db_path):
        pass
    with sqlite3.connect(str(db_path), timeout=15) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        if commit:
            conn.execute('BEGIN IMMEDIATE')
        else:
            conn.execute('BEGIN')
        _ensure_schema(conn)
        check_conflicts(conn,context,namespace=cfg['namespace'],model=cfg['model'])
        identity = (cfg['namespace'], cfg['satellite_instance'], cfg['model'], cfg['subsystem'],cfg['device'],cfg['variable'])
        found = conn.execute('''SELECT id,unit FROM v2_channels WHERE namespace=? AND satellite_instance=?
            AND model=? AND subsystem=? AND device=? AND variable=?''', identity).fetchone()
        if found and found[1] and found[1] != cfg['unit']:
            raise ValueError('同一逻辑参量单位已登记为 '+str(found[1])+'，拒绝混入不同单位')
        if found and not found[1] and conn.execute(
            "SELECT 1 FROM v2_segments WHERE channel_id=? LIMIT 1", (found[0],)).fetchone():
            raise ValueError('该历史参量缺少可信单位，请先治理单位元数据；不能用本次导入覆盖未知单位')
        if found:
            _existing_contract(conn, found[0], cfg, rows)
        result = {
            'status': 'imported' if commit else 'preview',
            'identity': dict(zip(('namespace','satellite_instance','model','subsystem','device','variable'), identity)),
            'role': cfg['role'], 'time_domain': cfg['time_domain'],
            'time_reference': cfg['time_reference'], 'time_unit': cfg['time_unit'],
            'row_count':len(rows), 'missing_count':sum(row[5] is None for row in rows), 'sha256':digest,
            'time_start':rows[0][0], 'time_end':rows[-1][0],
            'filename':cfg['filename'], 'unit':cfg['unit'],
            'quality':quality,'import_context':{k:v for k,v in (context or {}).items() if k!='metadata_json'},
        }
        if not commit:
            conn.rollback()
            return result
        if not found:
            channel_id = conn.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable,unit)
                VALUES(?,?,?,?,?,?,?)''',(*identity,cfg['unit'])).lastrowid
        else:
            channel_id = found[0]
            if not found[1]:
                conn.execute('UPDATE v2_channels SET unit=? WHERE id=?',(cfg['unit'],channel_id))
        source = 'upload_' + uuid4().hex
        sid = conn.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count)
             VALUES(?,?,?,?,?,?,?)''',(source,cfg['filename'],digest,size,
                datetime.now(timezone.utc).isoformat(),1,len(rows))).lastrowid
        record_context(conn,sid,context,quality)
        segment_id = conn.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
             time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
             VALUES(?,?,?,'telemetry',?,'user_explicit',?,1,?,?,?,?)''',
             (sid, cfg['filename'], channel_id,cfg['role'],cfg['time_domain'],json.dumps(fields),len(rows),rows[0][0],rows[-1][0])).lastrowid
        conn.execute('INSERT INTO v2_segment_clock(segment_id,reference,unit,declared_by) VALUES (?,?,?,?)',
                     (segment_id,cfg['time_reference'],cfg['time_unit'],'user_explicit'))
        conn.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
             time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
             ((segment_id,channel_id,row[8], row[0],row[1],cfg['time_domain'],row[2],row[3],row[4],row[5],
               json.dumps(row[5]),row[6],json.dumps(row[6]),row[7]) for i,row in enumerate(rows)))
        result.update(channel_id=channel_id,segment_id=segment_id,source_namespace=source)
        conn.commit()
        return result


def list_imports(db_path: Path, *, limit=30):
    """List administrator-explicit V2 ingestion batches, not legacy guesses."""
    if type(limit) is not int or not (1 <= limit <= 100):
        raise ValueError('limit 必须在 1～100 之间')
    with open_database(db_path) as conn:
        has_context=bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_import_context' AND type='table'").fetchone())
        extra=(',x.run_token,x.dataset_split,x.group_id,x.run_role,x.label_scope,x.evidence_origin,x.quality_json'
               if has_context else ',NULL AS run_token,NULL AS dataset_split,NULL AS group_id, '
               'NULL AS run_role,NULL AS label_scope,NULL AS evidence_origin,NULL AS quality_json')
        join='LEFT JOIN v2_import_context x ON x.source_id=s.id' if has_context else ''
        rows=conn.execute(f'''SELECT g.id AS segment_id, s.source_name, s.imported_utc,
               s.source_sha256, c.id AS channel_id, c.namespace, c.satellite_instance,
               c.model, c.subsystem, c.device, c.variable, c.unit,
               g.role, g.source_row_count, g.time_domain, g.time_start_raw, g.time_end_raw {extra}
               FROM v2_segments g JOIN v2_sources s ON s.id=g.source_id
               JOIN v2_channels c ON c.id=g.channel_id {join}
               WHERE g.role_provenance='user_explicit'
               ORDER BY g.id DESC LIMIT ?''',(limit,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item['quality']=json.loads(item.pop('quality_json')) if item.get('quality_json') else None
            result.append(item)
        return {'items':result, 'count':len(result)}
