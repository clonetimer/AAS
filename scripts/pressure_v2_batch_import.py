#!/usr/bin/env python3
"""Safe P50 import benchmark on an ISOLATED temporary V2 SQLite database.

Pass a real CSV/CSV.GZ and its targets archive. The script never writes to the
real AAS database or telemetry. It reports exact input/sample counts, resource
measurements, quality statistics, and committed-receipt idempotence. It makes
no claims about untested source runs or Windows performance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import tempfile
import threading
import time
from datetime import datetime, timezone

from scripts.build_v2_batch_manifest import build_plan
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_batch_import import import_file
from scripts.server.telemetry_v2_raw_rows import raw_store_health


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _atomic_json(path, obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    import uuid
    staged=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with staged.open('x',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(staged,path)
    finally:
        staged.unlink(missing_ok=True)


class MemorySampler:
    """Sample whole-process RSS; report unavailable without pretending otherwise."""
    def __init__(self):
        self.peak_bytes=None
        self._stop=threading.Event()
        self._thread=None
        try:
            import psutil
            self._process=psutil.Process(os.getpid())
        except ImportError:
            self._process=None

    def __enter__(self):
        if self._process:
            def sample():
                while not self._stop.is_set():
                    try:
                        rss=self._process.memory_info().rss
                        self.peak_bytes=max(rss,self.peak_bytes or 0)
                    except Exception:pass
                    self._stop.wait(.05)
            self._thread=threading.Thread(target=sample,daemon=True)
            self._thread.start()
        return self

    def __exit__(self,*_args):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)


def benchmark(source,targets_zip,*,model,unit_overrides=None,
              confirmed_normal=False,max_rows=2_000_000,max_extracted_bytes=1_000_000_000,
              temp_parent=None):
    """Exercise a single source through preview, commit, idempotent retry.

    Create and destroy a new V2 database. Excludes normal training runs with
    unreviewed missing labels. Never manufactures per-channel fault labels.
    """
    source=Path(source).resolve();targets_zip=Path(targets_zip).resolve()
    if not source.is_file() or not targets_zip.is_file():
        raise ValueError('源数据或 targets.zip 不存在')
    if type(confirmed_normal) is not bool:
        raise ValueError('confirmed_normal must be boolean')
    plan,archive_report=build_plan(source.parent,targets_zip,model=model,unit_overrides=unit_overrides)
    selection=[item for item in plan['files'] if item['path']==source.name]
    if len(selection)!=1:
        raise ValueError('所选文件不在可导入清单中（可能是无标签、重复 token 或训练集 FAULT）')
    cfg=selection[0]['config']
    if cfg['role']=='train' and cfg['import_context']['run_role']=='NORMAL':
        if not confirmed_normal:
            raise ValueError('无逐点标签的 NORMAL 训练源必须人工确认；请显式传 --confirm-normal')
        cfg['all_normal_confirmed']=True

    report={'schema_version':1,'status':'running','started_utc':_utc(),
            'source_filename':source.name,'source_bytes':source.stat().st_size,
            'targets_archive_members':archive_report['verified_targets_archive_members'],
            'run_token':cfg['import_context']['run_token'],
            'run_role':cfg['import_context']['run_role'],
            'dataset_split':cfg['import_context']['dataset_split'],
            'group_id':cfg['import_context']['group_id'],
            'label_scope':cfg['import_context']['label_scope'],
            'db_scope':'temporary_disposable_never_real_data',
            'input_role_semantics':'no_run_fault_to_channel_truth',
            'runtime':{'platform':os.name},'measurements':{}}
    with tempfile.TemporaryDirectory(prefix='aas-p50-pressure-',dir=temp_parent) as work:
        db=Path(work)/'isolated-v2.sqlite'
        with closing(sqlite3.connect(db)) as conn, conn:create_v2(conn)
        with MemorySampler() as sampler:
            t0=time.perf_counter()
            preview=import_file(db,source,cfg,dry_run=True,max_rows=max_rows,
                                max_extracted_bytes=max_extracted_bytes)
            t1=time.perf_counter()
            with closing(sqlite3.connect(db)) as conn, conn:
                assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==0
                assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0
            committed=import_file(db,source,cfg,max_rows=max_rows,max_extracted_bytes=max_extracted_bytes)
            t2=time.perf_counter()
            skipped=import_file(db,source,cfg,max_rows=max_rows,max_extracted_bytes=max_extracted_bytes)
            t3=time.perf_counter()
        report['measurements']={'preview_seconds':round(t1-t0,3),
            'commit_seconds':round(t2-t1,3),'repeat_seconds':round(t3-t2,3),
            'rss_peak_mb':round(sampler.peak_bytes/1024**2,2) if sampler.peak_bytes is not None else None,
            'rss_method':'psutil_process_rss' if sampler.peak_bytes is not None else 'unavailable',
            'sqlite_bytes':db.stat().st_size,
            'samples_per_second':round(committed['row_count']*committed['column_count']/max(t2-t1,1e-9),2),
            'timestamps_per_second':round(committed['row_count']/max(t2-t1,1e-9),2)}
        with closing(sqlite3.connect(db)) as conn, conn:
            counts={name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in
                    ('v2_sources','v2_segments','v2_samples','v2_batch_import_receipts')}
            actual_labels={('unknown' if label is None else str(label)):count for label,count in
                           conn.execute('SELECT label_int,COUNT(*) FROM v2_samples GROUP BY label_int')}
            actual_split=conn.execute('SELECT dataset_split,group_id,run_token,label_scope FROM v2_import_context').fetchone()
            storage=raw_store_health(conn)
            quick=conn.execute('PRAGMA quick_check').fetchone()[0]
            foreign_keys=conn.execute('PRAGMA foreign_key_check').fetchall()
        expected_count=preview['row_count']*preview['column_count']
        must_match=(committed['status']=='imported' and skipped['status']=='skipped' and
                    counts=={'v2_sources':1,'v2_segments':preview['column_count'],
                             'v2_samples':expected_count,'v2_batch_import_receipts':1} and
                    actual_split==(cfg['import_context']['dataset_split'],cfg['import_context']['group_id'],
                                   cfg['import_context']['run_token'],cfg['import_context']['label_scope']) and
                    quick=='ok' and not foreign_keys and storage['dangling_refs']==0 and
                    all((None if label=='unknown' else int(label)) in (None,0,1) for label in actual_labels))
        if preview['quality']['label_counts'].get('unknown')==preview['row_count']:
            must_match=must_match and actual_labels=={'unknown':expected_count}
        report.update({'status':'passed' if must_match else 'failed',
                       'preview_quality':preview['quality'],
                       'source_sha256':preview['sha256'],
                       'counts':counts,'raw_row_storage':storage,'actual_sample_labels':actual_labels,
                       'sqlite_quick_check':quick,'foreign_key_violations':len(foreign_keys),
                       'verification':'count_split_group_receipt_idempotent_and_integrity',
                       'limits':'one-source benchmark, not 3548-run throughput or Windows certification',
                       'completed_utc':_utc()})
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path)
    p.add_argument('--targets-zip',required=True,type=Path)
    p.add_argument('--model',required=True)
    p.add_argument('--unit-overrides',type=Path)
    p.add_argument('--confirm-normal',action='store_true')
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--max-rows',type=int,default=2_000_000)
    p.add_argument('--max-extracted-bytes',type=int,default=1_000_000_000)
    args=p.parse_args(argv)
    try:
        overrides=json.loads(args.unit_overrides.read_text(encoding='utf-8')) if args.unit_overrides else None
        result=benchmark(args.source,args.targets_zip,model=args.model,unit_overrides=overrides,
                         confirmed_normal=args.confirm_normal,max_rows=args.max_rows,
                         max_extracted_bytes=args.max_extracted_bytes)
        _atomic_json(args.report,result)
        print(json.dumps({k:result[k] for k in ('status','run_token','dataset_split','counts','measurements')},ensure_ascii=False,indent=2))
        return 0 if result['status']=='passed' else 1
    except (ValueError,OSError,sqlite3.Error,json.JSONDecodeError) as exc:
        result={'status':'failed','error':str(exc),'completed_utc':_utc()}
        _atomic_json(args.report,result)
        print(json.dumps(result,ensure_ascii=False),file=__import__('sys').stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
