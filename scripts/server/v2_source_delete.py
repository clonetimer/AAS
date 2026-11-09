"""Transactional deletion of one V2 source and its dependent rows."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

SOURCE_CHILDREN = ('v2_source_parameter_events','v2_import_context','v2_upload_manifest',
                   'v2_batch_import_receipts','v2_injection_records','v2_source_raw_rows')
SEGMENT_CHILDREN = {'v2_preprocess_quality':'output_segment_id',
                    'v2_preprocess_jobs':'output_segment_id', 'v2_samples':'segment_id',
                    'v2_legacy_rows':'segment_id','v2_issues':'segment_id',
                    'v2_segment_clock':'segment_id','v2_segment_governance':'segment_id'}
KNOWN = set(SOURCE_CHILDREN) | set(SEGMENT_CHILDREN) | {
    'v2_sources','v2_segments','v2_channels','v2_run_annotations',
    'v2_run_fault_locations','v2_fault_events','v2_governance_audit',
    'v2_meta','v2_source_deletion_audit'}


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _id(value):
    if type(value) is not int or value < 1: raise ValueError('source_id 必须是正整数')
    return value


def _model_blockers(root, database, source_id):
    path=Path(root)/'models'/'model.db'
    if not path.is_file():return []
    blockers=[]
    with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as db:
        if 'models' not in _tables(db):return []
        for mid,name,dataset,raw in db.execute('SELECT id,model_name,dataset,config_json FROM models'):
            if Path(str(dataset)).name != Path(database).name:continue
            try:
                config=json.loads(raw or '{}')
                contract=config.get('data_contract',config)
                ids=list((contract.get('v2_run_selection') or {}).get('source_ids') or [])
                ids += [r.get('source_id') for r in (contract.get('v2_run_manifest') or {}).get('runs',[]) if isinstance(r,dict)]
                ids += [r.get('source_id') for r in (contract.get('v2_expected_sources') or {}).get('train_run_evidence',[]) if isinstance(r,dict)]
            except (ValueError,TypeError,AttributeError):ids=[]
            if not ids or str(source_id) in {str(value) for value in ids}:
                blockers.append({'type':'model','id':mid,'name':name})
    return blockers


def list_sources(database,limit=500):
    if type(limit) is not int or not 1<=limit<=10000:raise ValueError('limit 必须为 1～10000')
    with sqlite3.connect('file:'+Path(database).as_posix()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute('''SELECT s.id AS source_id,s.namespace,s.source_name,s.source_sha256,
            s.source_row_count,s.source_table_count,s.imported_utc,COUNT(g.id) AS segment_count
            FROM v2_sources s LEFT JOIN v2_segments g ON g.source_id=s.id
            GROUP BY s.id ORDER BY s.id LIMIT ?''',(limit,))
        return {'database':Path(database).name,'items':[dict(r) for r in rows]}


def _snapshot(db,source_id):
    db.row_factory=sqlite3.Row
    r=db.execute('SELECT * FROM v2_sources WHERE id=?',(source_id,)).fetchone()
    if r is None:raise KeyError('数据源不存在')
    segments=db.execute('SELECT COUNT(*) FROM v2_segments WHERE source_id=?',(source_id,)).fetchone()[0]
    samples=db.execute('''SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g
                          ON g.id=s.segment_id WHERE g.source_id=?''',(source_id,)).fetchone()[0]
    return {'source_id':source_id,'namespace':r['namespace'],'source_name':r['source_name'],
            'source_sha256':r['source_sha256'],'source_rows':r['source_row_count'],
            'imported_utc':r['imported_utc'],
            'segments':segments,'samples':samples}


def _token(database,snapshot):
    raw=json.dumps({'database':Path(database).name,**snapshot},sort_keys=True,
                   ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _other_blockers(db,source_id,tables):
    blockers=[]
    if 'v2_preprocess_quality' in tables:
        count=db.execute('''SELECT COUNT(*) FROM v2_preprocess_quality q
          JOIN v2_segments input ON input.id=q.input_segment_id
          JOIN v2_segments output ON output.id=q.output_segment_id
          WHERE input.source_id=? AND output.source_id<>?''',(source_id,source_id)).fetchone()[0]
        if count:blockers.append({'type':'derived_source','count':count})
    for table in sorted(tables-KNOWN):
        if not table.startswith('v2_'):continue
        quoted='"'+table.replace('"','""')+'"'
        cols={r[1] for r in db.execute('PRAGMA table_info('+quoted+')')}
        if 'source_id' in cols and db.execute('SELECT 1 FROM '+quoted+' WHERE source_id=? LIMIT 1',(source_id,)).fetchone():
            blockers.append({'type':'unknown_dependency','table':table})
        for col in ('segment_id','input_segment_id','output_segment_id'):
            if col in cols and db.execute('SELECT 1 FROM '+quoted+' WHERE "'+col+'" IN (SELECT id FROM v2_segments WHERE source_id=?) LIMIT 1',(source_id,)).fetchone():
                blockers.append({'type':'unknown_dependency','table':table})
    return blockers


def preview(root,database,source_id):
    source_id=_id(source_id)
    with sqlite3.connect('file:'+Path(database).as_posix()+'?mode=ro',uri=True) as db:
        snapshot=_snapshot(db,source_id)
        blockers=_other_blockers(db,source_id,_tables(db))
    blockers += _model_blockers(root,database,source_id)
    return {**snapshot,'database':Path(database).name,'preview_token':_token(database,snapshot),
            'blockers':blockers,'deletable':not blockers}


def delete(root,database,source_id,*,preview_token,confirm_name,active_tasks=False,actor='unknown'):
    source_id=_id(source_id)
    if active_tasks:raise RuntimeError('有任务正在运行；请待任务结束后删除数据源')
    if not isinstance(preview_token,str) or len(preview_token)!=64:raise ValueError('请先预检数据源')
    with sqlite3.connect(database,timeout=90) as db:
        db.execute('PRAGMA foreign_keys=ON')
        registry=Path(root)/'models'/'model.db'
        if registry.is_file():
            # Lock both files until commit so a model cannot be published after
            # the dependency check but before this source disappears.
            db.execute('ATTACH DATABASE ? AS model_registry',(str(registry),))
        db.execute('BEGIN IMMEDIATE')
        snapshot=_snapshot(db,source_id)
        if confirm_name!=snapshot['source_name'] or preview_token!=_token(database,snapshot):
            raise ValueError('数据源名称或预检结果已变化，请重新预检')
        tables=_tables(db)
        blockers=_other_blockers(db,source_id,tables)+_model_blockers(root,database,source_id)
        if blockers:raise RuntimeError('数据源仍被引用，拒绝删除：'+json.dumps(blockers,ensure_ascii=False))
        if 'v2_run_fault_locations' in tables:db.execute('DELETE FROM v2_run_fault_locations WHERE source_id=?',(source_id,))
        if 'v2_run_annotations' in tables:db.execute('DELETE FROM v2_run_annotations WHERE source_id=?',(source_id,))
        for table in SOURCE_CHILDREN:
            if table in tables:db.execute('DELETE FROM '+table+' WHERE source_id=?',(source_id,))
        for table,col in SEGMENT_CHILDREN.items():
            if table in tables:db.execute('DELETE FROM '+table+' WHERE '+col+' IN (SELECT id FROM v2_segments WHERE source_id=?)',(source_id,))
        exclusive=[r[0] for r in db.execute('''SELECT DISTINCT g.channel_id FROM v2_segments g WHERE g.source_id=?
          AND g.channel_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM v2_segments other
          WHERE other.channel_id=g.channel_id AND other.source_id<>?)''',(source_id,source_id))]
        if exclusive and 'v2_fault_events' in tables:
            db.executemany('DELETE FROM v2_fault_events WHERE channel_id=?',[(cid,) for cid in exclusive])
        db.execute('DELETE FROM v2_segments WHERE source_id=?',(source_id,))
        if exclusive:db.executemany('DELETE FROM v2_channels WHERE id=?',[(cid,) for cid in exclusive])
        db.execute('DELETE FROM v2_sources WHERE id=?',(source_id,))
        problem=db.execute('PRAGMA foreign_key_check').fetchone()
        if problem:raise RuntimeError('外键检查失败，删除已回滚：'+repr(problem))
        db.execute('''CREATE TABLE IF NOT EXISTS v2_source_deletion_audit(
          id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL,namespace TEXT NOT NULL,
          source_name TEXT NOT NULL,source_sha256 TEXT NOT NULL,segments INTEGER NOT NULL,
          samples INTEGER NOT NULL,actor TEXT NOT NULL,deleted_utc TEXT NOT NULL)''')
        db.execute('''INSERT INTO v2_source_deletion_audit
          (source_id,namespace,source_name,source_sha256,segments,samples,actor,deleted_utc)
          VALUES(?,?,?,?,?,?,?,?)''',(source_id,snapshot['namespace'],snapshot['source_name'],
          snapshot['source_sha256'],snapshot['segments'],snapshot['samples'],
          str(actor or 'unknown')[:120],
          datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {'deleted':True,**snapshot,'database':Path(database).name,'removed_channels':len(exclusive)}
