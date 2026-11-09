"""P2: local, disk-staged, file-atomic V2 wide CSV/CSV.GZ import.

Not an HTTP upload handler. A trusted administrator explicitly supplies a manifest
and target database. Each file is parsed with bounded memory into an ephemeral
SQLite staging database, validated, and committed as ONE destination transaction.
The durable receipt is committed alongside the samples, so a retry skips only
an identical source + mapping/configuration. No fault truth is ever inferred.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

import csv
from contextlib import closing
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
import math
import re
import shutil
from pathlib import Path
import sqlite3
import statistics
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from scripts.rebuild_telemetry_v2 import parse_time
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_import import _config, _ensure_schema, _required
from scripts.server.telemetry_v2_import_context import parse_context, check_conflicts, record_context
from scripts.server.telemetry_v2_raw_rows import ensure_raw_store

MAX_FILES = 5000
MAX_COLUMNS = 64
DEFAULT_ROW_LIMIT = 2_000_000
MAX_ROW_LIMIT = 10_000_000
DEFAULT_EXTRACTED_BYTES = 1_000_000_000
MAX_EXTRACTED_BYTES = 4_000_000_000
CHUNK_ROWS = 256
RECEIPT_DDL = '''CREATE TABLE IF NOT EXISTS v2_batch_import_receipts(
  source_sha256 TEXT NOT NULL, config_sha256 TEXT NOT NULL,
  source_id INTEGER NOT NULL UNIQUE REFERENCES v2_sources(id),
  row_count INTEGER NOT NULL, column_count INTEGER NOT NULL,
  committed_utc TEXT NOT NULL,
  PRIMARY KEY(source_sha256,config_sha256)
)'''
INSERT_SAMPLES = '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,
  timestamp_raw,timestamp_type,time_domain,time_sort_text,time_sort_num,time_utc_us,
  value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''


class _CappedReader(io.RawIOBase):
    """Enforce a cap on actual decompressed bytes, not the .gz file size."""
    def __init__(self, source, limit):
        self._source, self.limit, self.total = source, limit, 0
    def readable(self): return True
    def readinto(self, buf):
        n = self._source.readinto(buf)
        if n:
            self.total += n
            if self.total > self.limit:
                raise ValueError('解压/读取数据超过配置上限，拒绝继续导入')
        return n


def _source_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for part in iter(lambda: src.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def _canonical_digest(config, filename):
    canonical = {**config, 'filename': filename}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _is_gzip_csv(filename):
    return re.fullmatch(r'[^/\\]+\.csv(?:\([0-9]+\))?\.gz',filename,re.I) is not None


def _read_csv(path, max_bytes):
    raw = gzip.open(path, 'rb') if _is_gzip_csv(path.name) else path.open('rb')
    guard = _CappedReader(raw, max_bytes)
    text = io.TextIOWrapper(io.BufferedReader(guard, buffer_size=64*1024), encoding='utf-8-sig', newline='')
    return text  # closes guard and gzip/regular source on context exit


def _mapped_columns(config, common, fields):
    time_col = next((col for col in ('timestamp','time_s') if col in fields), None)
    label_col = next((col for col in ('label','fault_label') if col in fields), None)
    if not time_col or len(fields) != len(set(fields)) or any(not x or x.startswith('__') for x in fields):
        raise ValueError('表头缺少 timestamp/time_s，或存在非法/重复列名')
    columns = [name for name in fields if name not in {time_col, label_col}]
    if not 1 <= len(columns) <= MAX_COLUMNS:
        raise ValueError('必须包含 1～64 个数值参量列')
    if label_col is None:
        if common['role'] == 'train' and config.get('all_normal_confirmed') is not True:
            raise ValueError('训练文件没有标签：必须人工确认全部正常')
        if common['role'] == 'test' and config.get('unlabeled_test_confirmed') is not True:
            raise ValueError('无标签测试文件必须显式确认未知标签')
        if common['role'] not in ('train','test'):
            raise ValueError('无标签文件仅允许明确的 train/test 用途')
    source_mapping = config.get('column_metadata') or {}
    if not isinstance(source_mapping, dict) or set(source_mapping) - set(columns):
        raise ValueError('column_metadata 必须与文件列匹配')
    if config.get('auto_map'):
        catalog_path = Path(__file__).resolve().parents[2] / 'web/visualization/telemetry-v2-column-catalog.json'
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))['items']
    else:
        catalog = {}
    complete={}
    for col in columns:
        mapping = {**catalog.get(col, {}), **(source_mapping.get(col) or {})}
        if set(mapping) - {'subsystem','device','variable','unit'}:
            raise ValueError(f'{col} 的列属性不合法')
        if set(mapping) != {'subsystem','device','variable','unit'}:
            raise ValueError(f'{col} 未能完整自动映射，须指定 subsystem/device/variable/unit')
        complete[col] = {key:_required(mapping[key],f'{col}.{key}',max_length=32 if key=='unit' else 100)
                         for key in ('subsystem','device','variable','unit')}
    ids = {(r['subsystem'],r['device'],r['variable']) for r in complete.values()}
    if len(ids)!=len(columns):
        raise ValueError('多列映射到同一参量，不允许合并')
    return time_col,label_col,columns,complete


def _stage_file(path, config, *, max_rows, max_bytes):
    """Stream once to disk; duplicates use UNIQUE indexed source time keys."""
    folder = tempfile.TemporaryDirectory(prefix='aas-v2-batch-stage-')
    stage_path = Path(folder.name) / 'stage.sqlite'
    try:
        conn = sqlite3.connect(stage_path)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('''CREATE TABLE source_rows(
          ordinal INTEGER PRIMARY KEY, time_key REAL NOT NULL UNIQUE,
          timestamp_raw TEXT NOT NULL, timestamp_type TEXT NOT NULL,
          time_sort_text TEXT, time_sort_num REAL, time_utc_us INTEGER,
          label_int INTEGER, raw_row_json TEXT NOT NULL)''')
        counts = {'0':0,'1':0,'unknown':0}
        missing = {}
        previous = None
        last = None
        reversal = 0
        rows_count = 0
        raw_json_bytes = 0
        first_stamp = None
        low_key = float('inf');high_key = float('-inf')
        labels = set()
        with _read_csv(path,max_bytes) as file:
            reader = csv.DictReader(file, strict=True)
            fields = reader.fieldnames or []
            common = _config({**config, 'filename':path.name,'variable':'_batch_',
                              'unit':config.get('unit') or 'unknown'}, allow_external_filename=True)
            time_col,label_col,columns,mapping = _mapped_columns(config,common,fields)
            missing={c:0 for c in columns}
            pending=[]
            for record in reader:
                if rows_count >= max_rows:
                    raise ValueError(f'文件超过 {max_rows} 个时间采样，请提高受管任务上限或拆分')
                if None in record or any(value is None for value in record.values()):
                    raise ValueError(f'CSV 第 {rows_count+2} 行列数不匹配')
                stamp=record[time_col].strip()
                domain,text_key,num_key,utc_key,type_name = parse_time(stamp)
                if domain!=common['time_domain']:
                    raise ValueError(f'第 {rows_count+2} 行时间域不匹配')
                key=utc_key if domain=='datetime_utc' else num_key
                if key is None or not math.isfinite(float(key)) or abs(float(key))>=2**53:
                    raise ValueError(f'第 {rows_count+2} 行时间坐标无效/超精度')
                if domain=='numeric_unspecified':
                    try:
                        if Decimal(stamp)!=Decimal(str(num_key)):
                            raise ValueError(f'第 {rows_count+2} 行时间浮点精度不足')
                    except InvalidOperation as exc:
                        raise ValueError('时间坐标非法') from exc
                label=record[label_col].strip() if label_col else ('0' if common['role']=='train' else None)
                if label is not None and (label not in ('0','1') or common['role']=='train' and label!='0'):
                    raise ValueError(f'第 {rows_count+2} 行标签错误/训练包含故障')
                label_int=None if label is None else int(label)
                if first_stamp is None:first_stamp=stamp
                last=stamp
                labels.add(label_int)
                counts['unknown' if label_int is None else str(label_int)]+=1
                if previous is not None:
                    if key<previous:reversal+=1
                previous=key
                low_key=min(low_key,key);high_key=max(high_key,key)
                # Validate every numeric measurement. Preserve all original cells
                # and unknown labels; no fault inference and no implicit filling.
                for col in columns:
                    value=record[col].strip()
                    if value.lower() in ('','na','n/a','nan','null'):
                        missing[col]+=1
                    else:
                        try: number=float(value)
                        except (TypeError,ValueError,OverflowError) as exc:
                            raise ValueError(f'第 {rows_count+2} 行 {col} 不是数值') from exc
                        if not math.isfinite(number):raise ValueError(f'第 {rows_count+2} 行 {col} 非有限数值')
                raw_json=json.dumps(record,ensure_ascii=False,sort_keys=True)
                raw_json_bytes+=len(raw_json.encode('utf-8'))
                pending.append((rows_count,float(key),stamp,type_name,text_key,num_key,utc_key,label_int,raw_json))
                rows_count+=1
                if len(pending)>=CHUNK_ROWS:
                    try:conn.executemany('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?,?,?)',pending)
                    except sqlite3.IntegrityError as exc:raise ValueError('文件时间戳重复，拒绝导入') from exc
                    pending.clear()
            if pending:
                try:conn.executemany('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?,?,?)',pending)
                except sqlite3.IntegrityError as exc:raise ValueError('文件时间戳重复，拒绝导入') from exc
        if rows_count==0:raise ValueError('源文件为空')
        conn.commit()
        # Estimate the nominal period using a bounded chronological sample;
        # count actual gaps over the *entire* sorted source without an array.
        scale=1e6 if common['time_domain']=='datetime_utc' else {
            's':1,'ms':1e3,'us':1e6,'ns':1e9}[common['time_unit']]
        gaps=0; last_sorted=None; sorted_deltas=[]
        for (key,) in conn.execute('SELECT time_key FROM source_rows ORDER BY time_key'):
            if last_sorted is not None and len(sorted_deltas)<100_000:
                sorted_deltas.append((key-last_sorted)/scale)
            last_sorted=key
        median=statistics.median(sorted_deltas) if sorted_deltas else None
        if median and median>0:
            last_sorted=None
            for (key,) in conn.execute('SELECT time_key FROM source_rows ORDER BY time_key'):
                if last_sorted is not None and (key-last_sorted)/scale>3*median:gaps+=1
                last_sorted=key
        conn.close()
        # Phase51: the original wide row is stored ONCE, shared by all
        # measurement channels. Indexes and WAL/journal space are also budgeted.
        estimated_bytes=int(raw_json_bytes*1.5 + rows_count*len(columns)*350)
        quality={'total_rows':rows_count,'label_counts':counts,'missing_by_column':missing,
                 'sample_period_median_seconds':median,'gaps_gt_3x_median':gaps,
                 'source_order_reversals':reversal,'quality_scope':'stream_import_sampled_period_no_fault_inference',
                 'sample_period_estimation':'first_100000_sorted_deltas',
                 'raw_row_json_utf8_bytes':raw_json_bytes,
                 'estimated_sqlite_growth_bytes':estimated_bytes,
                 'storage_estimate_kind':'conservative_shared_raw_row_plus_sample_indexes_not_exact_size'}
        context=parse_context({**config,'filename':path.name},filename=path.name,labels=labels,role=common['role'])
        return folder,stage_path,common,fields,columns,mapping,quality,context,rows_count,low_key,high_key
    except BaseException:
        try:conn.close()
        except Exception:pass
        folder.cleanup()
        raise


def _verify_channels(db, common, mapping, low, high, stage_name='stage'):
    """Check all prior active timestamps against disk-staged keys, no big sets."""
    from scripts.server.telemetry_v2_governance import active_clause
    refs={}
    field='time_utc_us' if common['time_domain']=='datetime_utc' else 'time_sort_num'
    for name, meta in mapping.items():
        identity=(common['namespace'],common['satellite_instance'],common['model'],
                  meta['subsystem'],meta['device'],meta['variable'])
        existing=db.execute('''SELECT id,unit FROM v2_channels WHERE namespace=? AND satellite_instance=?
          AND model=? AND subsystem=? AND device=? AND variable=?''',identity).fetchone()
        if existing:
            cid,unit=existing
            if unit!=meta['unit']:
                raise ValueError(f'{name} 单位与已导入参量不一致')
            segments=db.execute('''SELECT g.time_domain,c.reference,c.unit FROM v2_segments g
              LEFT JOIN v2_segment_clock c ON c.segment_id=g.id WHERE g.channel_id=?
              AND g.role=? AND g.kind='telemetry' AND g.usable_for_timeseries=1'''+active_clause(db,'g'),
              (cid,common['role'])).fetchall()
            for dom,ref,tm_unit in segments:
                if dom!=common['time_domain'] or (dom=='numeric_unspecified' and (ref,tm_unit)!=(common['time_reference'],common['time_unit'])):
                    raise ValueError(f'{name} 旧数据时基不一致')
            collision=db.execute(f'''SELECT 1 FROM v2_samples s
                  JOIN {stage_name}.source_rows r ON r.time_key=s.{field}
                  JOIN v2_segments g ON g.id=s.segment_id
                  WHERE s.channel_id=? AND g.role=? AND g.kind='telemetry'
                    AND g.usable_for_timeseries=1 AND s.time_domain=?
                    AND s.{field} BETWEEN ? AND ?'''+active_clause(db,'g')+' LIMIT 1',
                  (cid,common['role'],common['time_domain'],low,high)).fetchone()
            if collision:raise ValueError(f'{name} 与已有数据时间戳重叠；禁止覆盖或默默去重')
        refs[name]=(identity,existing[0] if existing else None)
    return refs


def import_file(database, file_path, config, *, dry_run=False, max_rows=DEFAULT_ROW_LIMIT,
                max_extracted_bytes=DEFAULT_EXTRACTED_BYTES):
    """One file, one destination commit. Retry is idempotent by signed receipt."""
    database=Path(database).resolve();file_path=Path(file_path).resolve()
    if not database.is_file():raise FileNotFoundError('目标 V2 数据库不存在，不允许自动创建')
    if not file_path.is_file() or not (file_path.name.lower().endswith('.csv') or _is_gzip_csv(file_path.name)):
        raise ValueError('P2 流式通道只支持本地 .csv/.csv.gz 文件')
    if not isinstance(config,dict):raise ValueError('config 必须是完整映射对象')
    if type(max_rows) is not int or not 1<=max_rows<=MAX_ROW_LIMIT:
        raise ValueError('max_rows 不在允许范围')
    if type(max_extracted_bytes) is not int or not 1<=max_extracted_bytes<=MAX_EXTRACTED_BYTES:
        raise ValueError('max_extracted_bytes 不在允许范围')
    with open_database(database):pass
    digest=_source_digest(file_path)
    config_digest=_canonical_digest(config,file_path.name)
    with closing(sqlite3.connect(database)) as probe:
        if probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_batch_import_receipts'").fetchone():
            old=probe.execute('SELECT source_id FROM v2_batch_import_receipts WHERE source_sha256=? AND config_sha256=?',(digest,config_digest)).fetchone()
            if old and not dry_run:
                return {'status':'skipped','reason':'already_committed','source_id':old[0], 'filename':file_path.name,'sha256':digest}
        if probe.execute('SELECT 1 FROM v2_sources WHERE source_sha256=?',(digest,)).fetchone():
            if not (old if 'old' in locals() else None):
                raise ValueError('同一原始文件已经通过其他配置/入口入库；拒绝重复导入')
    (holder,stage_path,common,fields,columns,mapping,quality,context,count,low,high) = _stage_file(
        file_path,config,max_rows=max_rows,max_bytes=max_extracted_bytes)
    try:
        if not dry_run:
            required=quality['estimated_sqlite_growth_bytes']
            reserve=max(128*1024*1024,int(required*.25))
            available=shutil.disk_usage(database.parent).free
            if available < required+reserve:
                raise ValueError(
                    f'目标磁盘可用空间不足：估算写入约 {required/1024**3:.2f} GiB，'
                    f'需额外保留 {reserve/1024**3:.2f} GiB，'
                    f'当前仅剩 {available/1024**3:.2f} GiB；未写入目标数据库')
        with closing(sqlite3.connect(database,timeout=90)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('ATTACH DATABASE ? AS stage',(str(stage_path),))
            db.execute('BEGIN IMMEDIATE' if not dry_run else 'BEGIN')
            _ensure_schema(db)
            check_conflicts(db,context,namespace=common['namespace'],model=common['model'])
            refs=_verify_channels(db,common,mapping,low,high)
            if dry_run:
                db.rollback()
                return {'status':'preview','filename':file_path.name,'sha256':digest,'row_count':count,
                        'column_count':len(columns),'quality':quality}
            db.execute(RECEIPT_DDL)
            # Recheck once inside write transaction. Any racing import is rejected.
            duplicated=db.execute('SELECT source_id FROM v2_batch_import_receipts WHERE source_sha256=? AND config_sha256=?',
                                  (digest,config_digest)).fetchone()
            if duplicated:
                db.rollback()
                return {'status':'skipped','source_id':duplicated[0],'filename':file_path.name}
            if db.execute('SELECT 1 FROM v2_sources WHERE source_sha256=?',(digest,)).fetchone():
                raise ValueError('导入时检测到同源并发重复；已取消')
            source_id=db.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
                imported_utc,source_table_count,source_row_count) VALUES(?,?,?,?,?,?,?)''',
                ('batch_'+uuid4().hex,file_path.name,digest,file_path.stat().st_size,
                 datetime.now(timezone.utc).isoformat(),1,count)).lastrowid
            record_context(db,source_id,context,quality)
            ensure_raw_store(db)
            # Raw source rows are immutable and stored exactly once per
            # file/source ordinal, not N times for N telemetry channels.
            db.execute('''INSERT INTO v2_source_raw_rows(source_id,source_ordinal,raw_row_json)
                SELECT ?,ordinal,raw_row_json FROM stage.source_rows''', (source_id,))
            refs_out={}
            for name,(identity,existing) in refs.items():
                meta=mapping[name]
                cid=existing if existing else db.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,
                    subsystem,device,variable,unit) VALUES(?,?,?,?,?,?,?)''',(*identity,meta['unit'])).lastrowid
                sid=db.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
                    time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                    VALUES(?,?,?,'telemetry',?,'user_explicit',?,1,?,?,NULL,NULL)''',
                    (source_id,name,cid,common['role'],common['time_domain'],json.dumps(fields,ensure_ascii=False),count)).lastrowid
                db.execute('INSERT INTO v2_segment_clock(segment_id,reference,unit,declared_by) VALUES(?,?,?,?)',
                           (sid,common['time_reference'],common['time_unit'],'user_explicit'))
                refs_out[name]=(sid,cid)
            batch=[]
            cursor=db.execute('SELECT ordinal,timestamp_raw,timestamp_type,time_sort_text,time_sort_num,time_utc_us,label_int,raw_row_json FROM stage.source_rows ORDER BY time_key')
            first=last=None
            for ordinal,stamp,type_name,text_key,num_key,utc_key,label,raw_json in cursor:
                if first is None:first=stamp
                last=stamp
                row=json.loads(raw_json)
                for name in columns:
                    value=row[name].strip()
                    number=None if value.lower() in ('','na','n/a','nan','null') else float(value)
                    sid,cid=refs_out[name]
                    batch.append((sid,cid,ordinal,stamp,type_name,common['time_domain'],text_key,num_key,utc_key,
                                  number,json.dumps(number),label,json.dumps(label),''))
                if len(batch)>=CHUNK_ROWS*len(columns):
                    db.executemany(INSERT_SAMPLES,batch);batch.clear()
            if batch:db.executemany(INSERT_SAMPLES,batch)
            db.executemany('UPDATE v2_segments SET time_start_raw=?,time_end_raw=? WHERE id=?',
                           [(first,last,sid) for sid,_ in refs_out.values()])
            db.execute('''INSERT INTO v2_batch_import_receipts
              (source_sha256,config_sha256,source_id,row_count,column_count,committed_utc)
              VALUES(?,?,?,?,?,?)''',(digest,config_digest,source_id,count,len(columns),datetime.now(timezone.utc).isoformat()))
            db.commit()
            return {'status':'imported','filename':file_path.name,'source_id':source_id,'sha256':digest,
                    'row_count':count,'column_count':len(columns),'quality':quality}
    finally:
        holder.cleanup()


def run_manifest(database, manifest_path, *, dry_run=False, stop_on_error=False,
                 max_rows=DEFAULT_ROW_LIMIT, max_extracted_bytes=DEFAULT_EXTRACTED_BYTES):
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=1 or not isinstance(manifest.get('files'),list):
        raise ValueError('批量清单必须为 schema_version=1 且包含 files 数组')
    files=manifest['files']
    if not 1<=len(files)<=MAX_FILES:raise ValueError(f'批量任务只能包含 1～{MAX_FILES} 个文件')
    root=manifest_path.parent
    seen=set();out=[]
    for index,item in enumerate(files):
        if not isinstance(item,dict) or set(item)!={'path','config'} or not isinstance(item['path'],str) or not item['path']:
            raise ValueError(f'批次第 {index+1} 项必须提供 path/config')
        target=(root/item['path']).resolve()
        if not is_relative_to(target, root):raise ValueError('清单文件只能引用自身目录及子目录文件')
        if target in seen:raise ValueError('清单里同一文件重复')
        seen.add(target)
        try:
            result=import_file(database,target,item['config'],dry_run=dry_run,max_rows=max_rows,
                               max_extracted_bytes=max_extracted_bytes)
            out.append(result)
        except Exception as exc:
            out.append({'status':'failed','filename':item['path'],'error':str(exc)})
            if stop_on_error:break
    return {'status':'complete' if all(r['status']!='failed' for r in out) else 'partial_failure',
            'files_total':len(files),'files_processed':len(out),'results':out,
            'counts':{s:sum(r['status']==s for r in out) for s in ('imported','skipped','preview','failed')},
            'restart_policy':'rerun_same_manifest; durable committed receipts cause exact-match skip'}
