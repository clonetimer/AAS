"""Create a clean, V2-only SQLite database from multi-channel simulation CSV.gz runs.

Each source file is an independent elapsed-time clock and satellite-instance
placeholder. Never infer absolute UTC, satellite model, splits or fault labels.

Usage:
  python scripts/build_uploaded_v2.py --input-dir DIR --output datasets/aas_telemetry_v2.sqlite
Refuses overwrite; the original compressed CSV inputs are never modified.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.server.telemetry_v2_governance import SCHEMA as GOVERNANCE_SCHEMA
from scripts.migrate_v2_calendar_view import VIEW_SQL


def sha256_file(path):
    hashobj=hashlib.sha256()
    with open(path,'rb') as h:
        for block in iter(lambda:h.read(1<<20),b''):
            hashobj.update(block)
    return hashobj.hexdigest()


def identity(column):
    # A named parameter remains traceable to its exact original CSV column.
    parts=column.split('.')
    if len(parts)>=3:
        return parts[0],parts[1],'.'.join(parts[2:])
    if len(parts)==2:
        return parts[0],'general',parts[1]
    raise ValueError(f'列名缺少明确分系统: {column}')


def unit(column):
    # Only suffixes grounded in the telemetry names, no conversion or guess.
    suffixes={'_rad_s':'rad/s','_m_s2':'m/s²','_m_s':'m/s','_bps':'bit/s',
      '_bits':'bit','_power_w':'W','_energy_j':'J','_temp_c':'°C','_deg':'deg',
      '_tesla':'T','_rate_bps':'bit/s','_m':'m'}
    for tail,render in suffixes.items():
        if column.endswith(tail) or (tail+'_') in column:
            return render
    return None


def build(input_dir:Path, output:Path):
    files=sorted(input_dir.glob('*.csv.gz'))
    if not files:raise ValueError('输入目录中没有 .csv.gz')
    output=output.resolve()
    if output.exists():raise FileExistsError(f'拒绝覆盖: {output}')
    output.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(output)
    summary={'files':[], 'run_count':len(files),'channels':0,'rows':0,'samples':0,'label_policy':'unlabeled',
             'role':'simulation','clock':'elapsed_seconds_per_run','schema_version':2}
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=OFF') # only for brand new recreatable output
        db.execute('PRAGMA temp_store=MEMORY')
        # Skip large secondary time indexes during bulk creation, build them once.
        # Preserve full SQL statements: schema comments may contain semicolons.
        # All initial CREATE INDEX statements are single lines in CANON_SCHEMA.
        create_tables_sql = "\n".join(
            line for line in CANON_SCHEMA.splitlines()
            if not line.lstrip().upper().startswith('CREATE INDEX ')
        )
        db.executescript(create_tables_sql)
        db.executescript(GOVERNANCE_SCHEMA)
        # Presentation calendar view, automatically present in every newly built V2 DB.
        db.execute(VIEW_SQL)
        db.execute('''CREATE TABLE IF NOT EXISTS v2_segment_clock(
            segment_id INTEGER PRIMARY KEY REFERENCES v2_segments(id),
            reference TEXT NOT NULL, unit TEXT NOT NULL, declared_by TEXT NOT NULL)''')
        db.execute('''CREATE TABLE v2_upload_manifest(
            source_id INTEGER PRIMARY KEY REFERENCES v2_sources(id),
            source_filename TEXT NOT NULL, original_columns_json TEXT NOT NULL,
            original_time_column TEXT NOT NULL, simulation_run_id TEXT NOT NULL,
            model_claim TEXT NOT NULL, labels_present INTEGER NOT NULL,
            notes TEXT NOT NULL)''')
        db.execute("INSERT INTO v2_meta(key,value) VALUES('schema_version','2')")
        db.execute("INSERT INTO v2_meta(key,value) VALUES('origin','new_csv_gz_build_no_legacy_data')")
        db.execute("INSERT INTO v2_meta(key,value) VALUES('created_utc',?)",(datetime.now(timezone.utc).isoformat(),))
        inserted=[]
        for count,path in enumerate(files,1):
            digest=sha256_file(path)
            run_id=path.name[:-len('.csv.gz')]
            namespace='uploaded_'+run_id
            with gzip.open(path,'rt',encoding='utf-8-sig',newline='') as stream:
                reader=csv.reader(stream,strict=True)
                columns=next(reader)
                if not columns or columns[0]!='time_s' or len(columns)!=len(set(columns)) or len(columns)<2:
                    raise ValueError(f'CSV 格式与预期不符: {path.name}')
                values=columns[1:]
                source_id=db.execute('''INSERT INTO v2_sources(
                   namespace,source_name,source_sha256,source_bytes,imported_utc,
                   source_table_count,source_row_count) VALUES(?,?,?,?,?,?,0)''',
                   (namespace,path.name,digest,path.stat().st_size,datetime.now(timezone.utc).isoformat(),len(values))).lastrowid
                db.execute('''INSERT INTO v2_upload_manifest VALUES(?,?,?,?,?,?,?,?)''',
                   (source_id,path.name,json.dumps(columns), 'time_s', run_id,'not_provided',0,
                    'Independent numerical elapsed time; do not merge between runs; 0/1 flags are telemetry, NOT fault labels.'))
                series=[]
                for name in values:
                    subsystem,device,variable=identity(name)
                    channel_id=db.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable,unit)
                       VALUES(?,?,?,?,?,?,?)''',
                       (namespace,run_id,'unspecified',subsystem,device,variable,unit(name))).lastrowid
                    segment_id=db.execute('''INSERT INTO v2_segments(
                       source_id,source_table,channel_id,kind,role,role_provenance,time_domain,
                       usable_for_timeseries,schema_sql,columns_json,source_row_count,time_start_raw,time_end_raw)
                       VALUES(?, ?, ?, 'telemetry','simulation','uploaded_explicit','numeric_unspecified',1,NULL,?,0,NULL,NULL)''',
                       (source_id,name,channel_id,json.dumps(['time_s',name]))).lastrowid
                    db.execute('INSERT INTO v2_segment_clock VALUES(?,?,?,?)',
                               (segment_id,'elapsed_run:'+run_id,'s','CSV time_s header plus separate source file'))
                    series.append((segment_id,channel_id))
                insert_sql='''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,
                   timestamp_raw,timestamp_type,time_domain,time_sort_text,time_sort_num,time_utc_us,
                   value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                   VALUES (?,?,? ,?,'str','numeric_unspecified',NULL,?,NULL,?, ?,NULL,'null','{}')'''
                batch=[]
                rows=0;start=None;prev=None;last=None
                for row in reader:
                    if len(row)!=len(columns):
                        raise ValueError(f'{path.name} 第 {rows+2} 行列数不匹配')
                    timestamp=row[0]
                    try: t=float(timestamp)
                    except ValueError as exc:raise ValueError(f'{path.name} 非法 time_s: {timestamp}') from exc
                    if not math.isfinite(t) or t<0 or (prev is not None and t<=prev):
                        raise ValueError(f'{path.name} time_s 必须非负、严格递增且有限: {timestamp}')
                    if start is None:start=timestamp
                    last=timestamp;prev=t
                    for (sid,cid),raw in zip(series,row[1:]):
                        try:v=float(raw)
                        except ValueError as exc:raise ValueError(f'{path.name} 遥测非数值 {raw}') from exc
                        # Preserve absent/nonfinite value as JSON text without injecting artificial samples.
                        if not math.isfinite(v):
                            v=None
                            value_json=json.dumps({'original':raw,'nonfinite':True})
                        else:
                            value_json=raw
                        batch.append((sid,cid,rows,timestamp,t,v,value_json))
                    rows+=1
                    if len(batch)>=5000:
                        db.executemany(insert_sql,batch);batch=[]
                if batch:db.executemany(insert_sql,batch)
                db.execute('UPDATE v2_sources SET source_row_count=? WHERE id=?',(rows,source_id))
                db.executemany('''UPDATE v2_segments SET source_row_count=?,time_start_raw=?,time_end_raw=? WHERE id=?''',
                               [(rows,start,last,sid) for sid,cid in series])
                db.commit()
                summary['files'].append({'filename':path.name,'sha256':digest,'time_rows':rows,
                    'channels':len(series),'time_start':start,'time_end':last,'namespace':namespace})
                summary['channels']+=len(series)
                summary['rows']+=rows
                summary['samples']+=rows*len(series)
                print(f'[{count}/{len(files)}] {path.name}: {rows} frames x {len(series)} channels', flush=True)
        # Bulk index creation after inserts saves significant execution time.
        db.executescript('''
          CREATE INDEX idx_v2_segments_channel ON v2_segments(channel_id,role,kind);
          CREATE INDEX idx_v2_sample_channel_time_num ON v2_samples(channel_id,time_domain,time_sort_num,segment_id);
          CREATE INDEX idx_v2_sample_segment ON v2_samples(segment_id,source_ordinal);
          CREATE INDEX idx_v2_issues_segment ON v2_issues(segment_id,severity);
        ''')
        db.commit()
        integ=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=db.execute('PRAGMA foreign_key_check').fetchall()
        if integ!='ok' or foreign:raise RuntimeError(f'integrity:{integ}, foreign:{foreign[:3]}')
        if db.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]!=summary['samples']:
            raise RuntimeError('样本数不一致')
        summary['integrity_check']=integ
        db.execute('PRAGMA journal_mode=DELETE')
        db.commit()
        return summary
    except BaseException:
        db.close();output.unlink(missing_ok=True)
        raise
    finally:
        try:db.close()
        except Exception:pass


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input-dir',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    report=build(args.input_dir,args.output)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
