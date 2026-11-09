"""Build a NEW canonical telemetry V2 SQLite file from legacy AAS SQLite files.

Non-destructive. No source write, no dedup, no invented time zone or identity.
Unmapped/wide tables are losslessly archived as raw rows in legacy_rows.

Run: python scripts/rebuild_telemetry_v2.py --db datasets/history_sample.db
Run: python scripts/rebuild_telemetry_v2.py --db datasets/history_sample.db --apply --out datasets/telemetry_v2.sqlite
Multiple --db accepted. --apply never overwrites the destination.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

SCHEMA_VERSION = 2
IDENTITY = ('model', 'subsystem', 'device', 'variable')
DEFAULT_METADATA = {'dataset_meta', 'fault_records', 'table_index', 'variable_catalog'}
CONSIDERED_EXTRA = {'id', 'created_at', *IDENTITY}
CANON_SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE v2_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE v2_sources(
 id INTEGER PRIMARY KEY, namespace TEXT NOT NULL UNIQUE,
 source_name TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 source_bytes INTEGER NOT NULL, imported_utc TEXT NOT NULL,
 source_table_count INTEGER NOT NULL, source_row_count INTEGER NOT NULL
);
CREATE TABLE v2_channels(
 id INTEGER PRIMARY KEY, namespace TEXT NOT NULL,
 satellite_instance TEXT NOT NULL DEFAULT '',
 model TEXT NOT NULL, subsystem TEXT NOT NULL,
 device TEXT NOT NULL, variable TEXT NOT NULL, unit TEXT,
 UNIQUE(namespace,satellite_instance,model,subsystem,device,variable)
);
CREATE TABLE v2_segments(
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES v2_sources(id),
 source_table TEXT NOT NULL, channel_id INTEGER REFERENCES v2_channels(id),
 kind TEXT NOT NULL CHECK(kind IN ('telemetry','unmapped','metadata')),
 role TEXT NOT NULL, role_provenance TEXT NOT NULL,
 time_domain TEXT, usable_for_timeseries INTEGER NOT NULL DEFAULT 0,
 coordinate_mode TEXT NOT NULL DEFAULT 'time' CHECK(coordinate_mode IN ('time','frame')),
 frame_sequence_id TEXT,
 schema_sql TEXT, columns_json TEXT NOT NULL,
 source_row_count INTEGER NOT NULL,
 time_start_raw TEXT, time_end_raw TEXT,
 UNIQUE(source_id,source_table)
);
CREATE TABLE v2_samples(
 id INTEGER PRIMARY KEY, segment_id INTEGER NOT NULL REFERENCES v2_segments(id),
 channel_id INTEGER NOT NULL REFERENCES v2_channels(id),
 source_ordinal INTEGER NOT NULL,
 frame_index INTEGER CHECK(frame_index IS NULL OR frame_index >= 1),
 timestamp_raw TEXT, timestamp_type TEXT,
 relative_seconds REAL,
 quality_flag TEXT CHECK(quality_flag IN ('ok','missing') OR quality_flag IS NULL),
 time_domain TEXT NOT NULL,
 time_sort_text TEXT, time_sort_num REAL, time_utc_us INTEGER,
 value_num REAL, value_raw_json TEXT NOT NULL,
 label_int INTEGER CHECK(label_int IN (0,1) OR label_int IS NULL),
 label_raw_json TEXT NOT NULL,
 raw_row_json TEXT NOT NULL,
 UNIQUE(segment_id, source_ordinal)
);
-- Phase 51: each wide source row is retained once. Existing inline raw JSON
-- remains readable; raw_row_json='' in v2_samples references this table.
CREATE TABLE v2_source_raw_rows(
 source_id INTEGER NOT NULL REFERENCES v2_sources(id) ON DELETE CASCADE,
 source_ordinal INTEGER NOT NULL, raw_row_json TEXT NOT NULL,
 PRIMARY KEY(source_id,source_ordinal)
) WITHOUT ROWID;
CREATE VIEW v2_sample_raw_resolved AS
 SELECT s.id AS sample_id,
 CASE WHEN s.raw_row_json='' THEN r.raw_row_json ELSE s.raw_row_json END AS raw_row_json
 FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
 LEFT JOIN v2_source_raw_rows r ON r.source_id=g.source_id AND r.source_ordinal=s.source_ordinal;
CREATE TABLE v2_legacy_rows(
 segment_id INTEGER NOT NULL REFERENCES v2_segments(id),
 source_ordinal INTEGER NOT NULL, raw_row_json TEXT NOT NULL,
 PRIMARY KEY(segment_id,source_ordinal)
) WITHOUT ROWID;
CREATE TABLE v2_issues(
 id INTEGER PRIMARY KEY, segment_id INTEGER REFERENCES v2_segments(id),
 severity TEXT NOT NULL, issue_code TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX idx_v2_segments_channel ON v2_segments(channel_id,role,kind);
CREATE INDEX idx_v2_sample_channel_time_text ON v2_samples(channel_id,time_domain,time_sort_text,segment_id);
CREATE INDEX idx_v2_sample_channel_time_num ON v2_samples(channel_id,time_domain,time_sort_num,segment_id);
CREATE INDEX idx_v2_sample_segment ON v2_samples(segment_id,source_ordinal);
CREATE INDEX idx_v2_segments_coordinate ON v2_segments(coordinate_mode,frame_sequence_id,role);
CREATE UNIQUE INDEX idx_v2_sample_frame ON v2_samples(segment_id,frame_index) WHERE frame_index IS NOT NULL;
CREATE INDEX idx_v2_issues_segment ON v2_issues(segment_id,severity);
'''


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def json_value(value):
    if isinstance(value, bytes):
        return {'__sqlite_blob_hex__': value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {'__nonfinite_float__': repr(value)}
    return value


def encode(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fd:
        for block in iter(lambda: fd.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def table_rows(conn, name: str):
    cols = [r[1] for r in conn.execute('PRAGMA table_info(' + quote(name) + ')')]
    if not cols:
        # SQLite generated-column-only or virtual tables cannot be safely inferred.
        raise ValueError(f'表字段列表为空：{name}')
    for i, record in enumerate(conn.execute('SELECT * FROM ' + quote(name))):
        yield i, {k: json_value(v) for k, v in zip(cols, record)}


def columns_of(conn, name):
    return [r[1] for r in conn.execute('PRAGMA table_info(' + quote(name) + ')')]


def identity_catalog(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'aas_series_catalog','aas_variables','aas_devices','aas_subsystems','aas_models'} <= names:
        sql = '''SELECT c.table_name, m.name, s.name, d.name, v.name
                 FROM aas_series_catalog c JOIN aas_variables v ON c.variable_id=v.id
                 JOIN aas_devices d ON v.device_id=d.id
                 JOIN aas_subsystems s ON d.subsystem_id=s.id
                 JOIN aas_models m ON s.model_id=m.id'''
        return {r[0]: tuple(r[1:]) for r in conn.execute(sql)}
    if 'aas_series_catalog' in names:
        cols = set(columns_of(conn, 'aas_series_catalog'))
        if set(IDENTITY) <= cols:
            return {r[0]: tuple(r[1:]) for r in conn.execute(
                'SELECT table_name,model,subsystem,device,variable FROM aas_series_catalog')}
    return {}


def roles_catalog(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'aas_table_meta' not in names:
        return {}
    return {r[0]: str(r[1]) for r in conn.execute('SELECT table_name,declared_role FROM aas_table_meta')}


def assigned_role(table: str, roles: dict, source_name: str):
    if table in roles and roles[table] in ('train','test'):
        return roles[table], 'aas_table_meta'
    lower = table.lower()
    if lower.endswith('_test') or lower.endswith('_test_holdout'):
        return 'test', 'name_hint_only'
    if lower.endswith('_train'):
        return 'train', 'name_hint_only'
    if lower.endswith('_clean'):
        return 'processed', 'name_hint_only'
    if '_fault_' in lower and source_name == 'simulation.db':
        return 'simulated_fault', 'name_hint_only'
    if source_name == 'simulation.db':
        return 'simulation', 'database_hint_only'
    return 'unspecified', 'none'


def parse_time(value):
    """Return (domain, canonical_sort_text, numeric_sort, utc_us, original_type).

    Numeric timestamps are NEVER presumed epoch seconds or UTC.
    Naive datetime is NEVER presumed UTC. Unparseable timestamps are retained.
    """
    t = 'null' if value is None else type(value).__name__
    if value is None or isinstance(value, dict):
        return ('invalid',None,None,None,t)
    if isinstance(value, (int,float)) and not isinstance(value, bool):
        if math.isfinite(float(value)):
            return ('numeric_unspecified',None,float(value),None,t)
        return ('invalid',None,None,None,t)
    if not isinstance(value,str) or not value.strip():
        return ('invalid',None,None,None,t)
    text = value.strip()
    try:
        number = Decimal(text)
        if number.is_finite() and abs(number) < Decimal('1e300'):
            return ('numeric_unspecified',None,float(number),None,t)
    except (InvalidOperation,ValueError,OverflowError):
        pass
    try:
        if len(text) < 11 or (text[10] not in ('T',' ')):
            raise ValueError('datetime needs explicit date+time')
        dt = datetime.fromisoformat(text.replace('Z','+00:00'))
        if dt.tzinfo is None:
            return ('datetime_naive',dt.isoformat(timespec='microseconds'),None,None,t)
        utc = dt.astimezone(timezone.utc)
        delta = utc - datetime(1970,1,1,tzinfo=timezone.utc)
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        if abs(micros) > 9e18:
            return ('invalid',None,None,None,t)
        return ('datetime_utc',utc.isoformat(timespec='microseconds'),None,micros,t)
    except (ValueError,OverflowError):
        return ('invalid',None,None,None,t)


def normalize_identity(conn, table, cols, catalog_map):
    recorded = catalog_map.get(table)
    has_columns = set(IDENTITY) <= set(cols)
    if recorded is not None and (len(recorded) != 4 or not all(isinstance(x,str) and x.strip() for x in recorded)):
        return None,'invalid_catalog_identity'
    values = None
    if has_columns:
        q = ','.join(quote(k) for k in IDENTITY)
        rows = conn.execute(f'SELECT DISTINCT {q} FROM {quote(table)} LIMIT 2').fetchall()
        if len(rows) > 1:
            return None,'mixed_identity_in_rows'
        if rows:
            values = tuple(rows[0])
            if not all(isinstance(x,str) and x.strip() for x in values):
                return None,'null_or_empty_identity'
    if recorded and values and recorded != values:
        return None,'catalog_row_identity_mismatch'
    if recorded:
        return recorded,None
    if values:
        return values,None
    return None,'identity_not_declared'


def classify(conn, table, cols, catalog_map):
    if table.startswith('aas_') or table in DEFAULT_METADATA:
        return 'metadata',None,'system_metadata'
    minimum = {'timestamp','value','label'}
    if not minimum <= set(cols):
        return 'unmapped',None,'not_standard_timestamp_value_label'
    extra_measurements = set(cols) - minimum - CONSIDERED_EXTRA
    if extra_measurements:
        return 'unmapped',None,'extra_measurement_columns:' + ','.join(sorted(extra_measurements))
    identity,problem = normalize_identity(conn,table,cols,catalog_map)
    if problem:
        return 'unmapped',None,problem
    return 'telemetry',identity,None


@contextmanager
def input_snapshot(source: Path):
    """Snapshot a read-only SQLite source, including committed WAL state."""
    with TemporaryDirectory(prefix='aas_v2_snapshot_') as tmp:
        db = Path(tmp) / 'source.sqlite'
        uri = f'{source.resolve().as_uri()}?mode=ro'
        with closing(sqlite3.connect(uri,uri=True)) as source_db, \
             closing(sqlite3.connect(db)) as output_db, output_db:
            source_db.backup(output_db)
        with closing(sqlite3.connect(db)) as snap:
            yield snap, file_hash(db), db.stat().st_size


def scan_source(source: Path) -> dict:
    with input_snapshot(source) as (conn, _, _):
        catalogs=identity_catalog(conn)
        role=roles_catalog(conn)
        items=[]
        for table,sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols=columns_of(conn,table)
            kind,ident,reason=classify(conn,table,cols,catalogs)
            count=conn.execute('SELECT COUNT(*) FROM '+quote(table)).fetchone()[0]
            r,how=assigned_role(table,role,source.name)
            items.append(dict(table=table,kind=kind,identity=ident,reason=reason,rows=count,role=r,role_source=how))
        return {'source':source.name,'tables':len(items),'rows':sum(x['rows'] for x in items),
                'classified':dict(Counter(x['kind'] for x in items)),'items':items}


def create_v2(destination):
    destination.executescript(CANON_SCHEMA)
    destination.execute('INSERT INTO v2_meta VALUES (?,?)',('schema_version',str(SCHEMA_VERSION)))
    destination.execute('INSERT INTO v2_meta VALUES (?,?)',('policy','preserve_all_rows_no_auto_merge_or_utc_inference'))
    from scripts.server.telemetry_v2_coordinates import ensure_coordinate_schema
    ensure_coordinate_schema(destination)


def add_issue(conn, segment, code, detail, severity='warning'):
    conn.execute('INSERT INTO v2_issues(segment_id,severity,issue_code,detail) VALUES (?,?,?,?)',
                 (segment,severity,code,detail))


def build_sources(sources: list[Path], target: Path) -> dict:
    seen=set()
    for src in sources:
        if src.stem in seen:
            raise ValueError(f'不同文件的库名相同，无法安全区分命名空间：{src.stem}')
        seen.add(src.stem)
    target = target.resolve()
    if target.exists() or target.with_name(target.name+'.partial').exists():
        raise FileExistsError('拒绝覆盖已有目标文件/临时文件：'+str(target))
    if any(src.resolve()==target for src in sources):
        raise ValueError('目标不能与来源数据库相同')
    target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_name(target.name+'.partial')
    reports=[]
    try:
        with closing(sqlite3.connect(partial)) as output, output:
            output.execute('PRAGMA foreign_keys=ON')
            output.execute('PRAGMA journal_mode=DELETE')
            create_v2(output)
            for source in sources:
                with input_snapshot(source) as (conn,snapshot_hash,snapshot_size):
                    result=import_source(conn,output,source,snapshot_hash,snapshot_size)
                    reports.append(result)
            actual=output.execute('SELECT (SELECT COUNT(*) FROM v2_samples)+(SELECT COUNT(*) FROM v2_legacy_rows)').fetchone()[0]
            expected=sum(r['rows'] for r in reports)
            if actual != expected:
                raise ValueError(f'迁移校验失败：源记录 {expected} / 新库记录 {actual}')
            if output.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('SQLite integrity_check 失败')
            if output.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('外键检查失败')
            output.commit()
        os.replace(partial,target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {'output':str(target),'schema_version':SCHEMA_VERSION,'sources':reports,
            'rows_preserved':sum(r['rows'] for r in reports),
            'canonical_samples':sum(r.get('canonical_rows',0) for r in reports),
            'legacy_rows':sum(r.get('legacy_rows',0) for r in reports),
            'source_tables':sum(r['tables'] for r in reports),
            'integrity':'ok','auto_deduplicated':0,'original_sources_modified':False}


def import_source(conn,out,source,hashsum,size):
    rows=conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    catalogs=identity_catalog(conn)
    roles=roles_catalog(conn)
    total=sum(conn.execute('SELECT COUNT(*) FROM '+quote(table)).fetchone()[0] for table,_ in rows)
    imported=datetime.now(timezone.utc).isoformat()
    sid=out.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count)
                       VALUES (?,?,?,?,?,?,?)''',(source.stem,source.name,hashsum,size,imported,len(rows),total)).lastrowid
    results=Counter()
    blocked=[]
    for table,sql in rows:
        cols=columns_of(conn,table)
        kind,identity,reason=classify(conn,table,cols,catalogs)
        role,role_how=assigned_role(table,roles,source.name)
        cid=None
        if identity:
            out.execute('''INSERT OR IGNORE INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable)
                           VALUES (?,'',?,?,?,?)''',(source.stem,*identity))
            cid=out.execute('''SELECT id FROM v2_channels WHERE namespace=? AND satellite_instance='' AND model=? AND subsystem=? AND device=? AND variable=?''',(source.stem,*identity)).fetchone()[0]
        count=conn.execute('SELECT COUNT(*) FROM '+quote(table)).fetchone()[0]
        seg=out.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,schema_sql,columns_json,source_row_count)
                           VALUES(?,?,?,?,?,?,?,?,?)''',(sid,table,cid,kind,role,role_how,sql,encode(cols),count)).lastrowid
        if reason and kind!='metadata':
            add_issue(out,seg,'unmapped_table',reason,'blocker')
            blocked.append({'table':table,'rows':count,'reason':reason})
        if role_how=='name_hint_only' and kind=='telemetry':
            add_issue(out,seg,'role_inferred','按表名识别用途，需人工确认后才可作为正式训练/测试数据')
        if role=='unspecified' and kind=='telemetry':
            add_issue(out,seg,'role_unassigned','数据用途未明确，不应自动进入训练或评估')
        domains=Counter(); invalid_values=0;invalid_labels=0
        minimum_key=None;maximum_key=None;tfirst=None;tlast=None
        for ordinal,raw in table_rows(conn,table):
            raw_json=encode(raw)
            if kind!='telemetry':
                out.execute('INSERT INTO v2_legacy_rows(segment_id,source_ordinal,raw_row_json) VALUES(?,?,?)',(seg,ordinal,raw_json))
                results['legacy_rows']+=1
                continue
            time_val=raw.get('timestamp')
            domain,sort_text,sort_num,utc_us,t_type=parse_time(time_val)
            domains[domain]+=1
            raw_value=raw.get('value'); raw_label=raw.get('label')
            try:
                v=float(raw_value)
                if not math.isfinite(v): raise ValueError('nonfinite')
            except (ValueError,TypeError):
                v=None
                invalid_values+=1
            if raw_label in (0,1,'0','1') and not isinstance(raw_label,bool):
                lab=int(raw_label)
            else:
                lab=None
                invalid_labels+=1
            out.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
                time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(seg,cid,ordinal,str(time_val) if time_val is not None else None,
                    t_type,domain,sort_text,sort_num,utc_us,v,encode(raw_value),lab,encode(raw_label),raw_json))
            results['canonical_rows']+=1
            if domain!='invalid':
                time_key = utc_us if domain=='datetime_utc' else (sort_num if domain=='numeric_unspecified' else sort_text)
                keyed=(domain,time_key)
                if minimum_key is None or keyed < minimum_key:
                    minimum_key=keyed;tfirst=str(time_val)
                if maximum_key is None or keyed > maximum_key:
                    maximum_key=keyed;tlast=str(time_val)
        if kind=='telemetry':
            ready = int(len(domains)==1 and 'invalid' not in domains and count>0 and invalid_values==0)
            domain=next(iter(domains)) if len(domains)==1 else 'mixed_or_empty'
            out.execute('''UPDATE v2_segments SET time_domain=?,usable_for_timeseries=?,time_start_raw=?,time_end_raw=? WHERE id=?''',
                (domain,ready,tfirst if len(domains)==1 else None,tlast if len(domains)==1 else None,seg))
            if not ready:
                add_issue(out,seg,'timeline_or_value_invalid',f'domains={dict(domains)}, invalid_values={invalid_values}', 'blocker')
            if invalid_labels:
                add_issue(out,seg,'labels_not_binary',f'{invalid_labels} 个样本没有有效 0/1 标签')
            if domain=='datetime_naive':
                add_issue(out,seg,'timezone_unknown','采样时间没有时区信息，不能自动认定 UTC')
            elif domain=='numeric_unspecified':
                add_issue(out,seg,'numeric_epoch_unknown','数值时间戳的起点、单位或时间系未声明，禁止与绝对 UTC 混用')
        results['tables']+=1
        results['rows']+=count
        results[kind+'_tables']+=1
    assert results['rows']==total
    return {'source':source.name,'namespace':source.stem,**dict(results),'unmapped':blocked}


def query_channel(db_path,namespace,model,subsystem,device,variable,*,start=None,end=None,roles=None,limit=1000,allow_inferred_roles=False,satellite_instance=''):
    """New API candidate: read a unique channel across eligible segments.

    No automatic source role mixing, no timezone conversion, no dedup.
    The caller must explicitly allow roles and/or handle conflicting observations.
    """
    if roles is None or not roles or any(x not in ('train','test','simulation','unspecified','processed','simulated_fault') for x in roles):
        raise ValueError('必须显式提供允许的数据用途 roles')
    if not 1<=limit<=200000:
        raise ValueError('limit 范围必须是 1~200000')
    with sqlite3.connect('file:'+Path(db_path).resolve().as_posix()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        cid=conn.execute('''SELECT id FROM v2_channels WHERE namespace=? AND satellite_instance=? AND model=? AND subsystem=? AND device=? AND variable=?''',
            (namespace,satellite_instance,model,subsystem,device,variable)).fetchone()
        if not cid:return []
        segs=conn.execute('SELECT id,time_domain,role,role_provenance FROM v2_segments WHERE channel_id=? AND kind=? AND usable_for_timeseries=1',
                          (cid['id'],'telemetry')).fetchall()
        eligible=[s for s in segs if s['role'] in roles and (allow_inferred_roles or s['role_provenance'] not in ('name_hint_only','database_hint_only') or s['role'] not in ('train','test'))]
        domains={s['time_domain'] for s in eligible}
        if len(domains)>1:
            raise ValueError('同参量分片使用不同时间基准，必须先显式映射后查询')
        if not eligible:return []
        domain=next(iter(domains))
        ids=[s['id'] for s in eligible]
        field='time_sort_num' if domain=='numeric_unspecified' else ('time_utc_us' if domain=='datetime_utc' else 'time_sort_text')
        if start is not None or end is not None:
            if domain=='datetime_utc':
                def conv(x):
                    tm=parse_time(x)
                    if tm[0]!='datetime_utc':raise ValueError('时间筛选条件必须含明确时区')
                    return tm[3]
            elif domain=='datetime_naive':
                def conv(x):
                    tm=parse_time(x)
                    if tm[0]!='datetime_naive':raise ValueError('时间筛选条件必须使用无时区本地时刻')
                    return tm[1]
            else:
                def conv(x):return float(x)
        cond=[f's.segment_id IN ({",".join("?" for _ in ids)})']
        args=list(ids)
        if start is not None:cond.append(f's.{field} >= ?');args.append(conv(start))
        if end is not None:cond.append(f's.{field} <= ?');args.append(conv(end))
        sql='''SELECT g.source_table,g.role,s.source_ordinal,s.timestamp_raw,s.value_num,s.label_int,s.value_raw_json
               FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id WHERE '''+' AND '.join(cond)+f' ORDER BY s.{field},g.source_table,s.source_ordinal LIMIT ?'
        args.append(limit)
        return [dict(x) for x in conn.execute(sql,args)]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,action='append',type=Path,help='原数据库路径（可多次指定）')
    parser.add_argument('--out',type=Path,help='新库路径（必须与源库不同）')
    parser.add_argument('--apply',action='store_true',help='执行新库构建，默认仅生成扫描报告')
    parser.add_argument('--report',type=Path,help='JSON 扫描/迁移报告保存位置')
    args=parser.parse_args(argv)
    sources=[x.resolve() for x in args.db]
    for source in sources:
        if not source.is_file() or source.suffix.lower() not in ('.db','.sqlite','.sqlite3'):
            parser.error('输入数据库不存在或扩展名不合法：'+str(source))
    if len(set(sources))!=len(sources):parser.error('来源数据库不能重复')
    if args.apply:
        if args.out is None:parser.error('--apply 必须显式提供 --out')
        result=build_sources(sources,args.out)
    else:
        result={'dry_run':True,'sources':[scan_source(x) for x in sources]}
    rendered=json.dumps(result,ensure_ascii=False,indent=2)
    print(rendered)
    if args.report:
        if args.report.exists():parser.error('拒绝覆盖已有报告：'+str(args.report))
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(rendered+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    main()
