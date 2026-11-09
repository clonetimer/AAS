"""P2 disk-stage import: no partial artifacts after failure, real idempotent resume."""
from __future__ import annotations
import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_batch_import import import_file, run_manifest


def database(tmp_path):
    db=tmp_path/'v2.sqlite'
    with sqlite3.connect(db) as conn:create_v2(conn)
    return db


def config(name='one', *, group=None, split=None):
    obj={'namespace':name,'model':'TEST-SAT','subsystem':'ADCS','device':'rw',
         'unit':'rpm','role':'test','time_domain':'numeric_unspecified',
         'time_reference':'test-clock','time_unit':'s','auto_map':True,
         'unlabeled_test_confirmed':True}
    if group:
        obj['import_context']={'run_token':'a'*24 if name=='one' else 'b'*24,
                               'dataset_split':split,'group_id':group,
                               'origin_type':'simulation','label_scope':'unknown'}
    return obj


def csv_gz(tmp_path, name='one.csv.gz', rows=4, *, fault=False, bad=False, unlabeled=False):
    file=tmp_path/name
    text='timestamp,adcs.pointing_error_deg,eps.bus_load_power_w'+('' if unlabeled else ',label')+'\n'
    for i in range(rows):
        fields=[str(i),'oops' if bad and i==rows-1 else str(12+i%9),str(5+i%2)]
        if not unlabeled:fields.append('1' if fault and i>=2 else '0')
        text+=','.join(fields)+'\n'
    with gzip.open(file,'wt',encoding='utf-8',newline='') as out:out.write(text)
    return file


def counts(db):
    with sqlite3.connect(db) as c:
        return {table:c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('v2_sources','v2_segments','v2_samples')}


def test_big_file_streaming_and_exact_resume(tmp_path):
    db=database(tmp_path);file=csv_gz(tmp_path,rows=50_002)
    preview=import_file(db,file,config(),dry_run=True)
    assert preview['row_count']==50_002
    assert counts(db)=={'v2_sources':0,'v2_segments':0,'v2_samples':0}
    result=import_file(db,file,config())
    assert result['row_count']==50_002 and result['column_count']==2
    assert counts(db)=={'v2_sources':1,'v2_segments':2,'v2_samples':100_004}
    assert import_file(db,file,config())['status']=='skipped'
    assert counts(db)['v2_sources']==1
    with sqlite3.connect(db) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_invalid_row_rolls_back_file_and_next_file_recovers(tmp_path):
    db=database(tmp_path);bad=csv_gz(tmp_path,'bad.csv.gz',bad=True);good=csv_gz(tmp_path,'good.csv.gz')
    manifest=tmp_path/'batch.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':[
        {'path':bad.name,'config':config('bad')},{'path':good.name,'config':config('good')}]},ensure_ascii=False))
    out=run_manifest(db,manifest)
    assert out['status']=='partial_failure' and out['counts']=={'imported':1,'skipped':0,'preview':0,'failed':1}
    assert counts(db)=={'v2_sources':1,'v2_segments':2,'v2_samples':8}
    assert run_manifest(db,manifest)['counts']=={'imported':0,'skipped':1,'preview':0,'failed':1}


def test_cross_file_collision_rolls_back(tmp_path):
    db=database(tmp_path);first=csv_gz(tmp_path,'first.csv.gz');second=csv_gz(tmp_path,'second.csv.gz',fault=True)
    import_file(db,first,config())
    with pytest.raises(ValueError,match='重叠'):
        import_file(db,second,config())
    assert counts(db)['v2_sources']==1


def test_labels_remain_original_and_context_is_recorded(tmp_path):
    db=database(tmp_path);file=csv_gz(tmp_path,fault=True)
    cfg=config(group='c'*24,split='validation')
    out=import_file(db,file,cfg)
    with sqlite3.connect(db) as conn:
        labels=conn.execute('SELECT DISTINCT label_int FROM v2_samples ORDER BY label_int').fetchall()
        assert [row[0] for row in labels]==[0,1]
        meta=conn.execute('SELECT run_token,dataset_split,group_id,label_scope FROM v2_import_context WHERE source_id=?',(out['source_id'],)).fetchone()
        assert meta==('a'*24,'validation','c'*24,'unknown')
    assert import_file(db,file,cfg)['status']=='skipped'


def test_unlabeled_test_preserves_unknown(tmp_path):
    db=database(tmp_path);file=csv_gz(tmp_path,unlabeled=True)
    out=import_file(db,file,config())
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int IS NULL').fetchone()[0]==8
    assert out['quality']['label_counts']['unknown']==4


def test_reject_duplicate_time_bad_column_and_different_digest_mapping(tmp_path):
    db=database(tmp_path);file=tmp_path/'same.csv.gz'
    with gzip.open(file,'wt') as f:f.write('timestamp,eps.bus_load_power_w,label\n1,2,0\n1,3,1\n')
    with pytest.raises(ValueError,match='重复'):import_file(db,file,config())
    assert counts(db)['v2_sources']==0
    good=csv_gz(tmp_path)
    import_file(db,good,config())
    with pytest.raises(ValueError,match='原始文件已经'):import_file(db,good,{**config(),'namespace':'changed'})
    unrecognized=tmp_path/'unknown.csv.gz'
    with gzip.open(unrecognized,'wt') as f:f.write('timestamp,mystery,label\n1,2,0\n')
    with pytest.raises(ValueError,match='未能完整自动映射'):import_file(db,unrecognized,config())


def test_manifest_path_traversal_rejected(tmp_path):
    db=database(tmp_path); manifest=tmp_path/'batch.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':[{'path':'../secret.csv.gz','config':config()}]}))
    with pytest.raises(ValueError,match='自身目录'):run_manifest(db,manifest)


def test_generate_reviewable_targets_manifest_without_train_fault_leakage(tmp_path):
    import zipfile
    from scripts.build_v2_batch_manifest import build_plan
    src=tmp_path/'runs';src.mkdir()
    target_zip=tmp_path/'targets.zip'
    parts=[('a'*24,'NORMAL','train'),('b'*24,'FAULT','train'),('c'*24,'FAULT','validation')]
    with zipfile.ZipFile(target_zip,'w') as archive:
        for i,(token,role,split) in enumerate(parts):
            target={'token':token,'split':split,'group':str(i)*24,'role':role,
                    'fault':None if role=='NORMAL' else 'motor_bias',
                    'component':None if role=='NORMAL' else 'adcs.rw',
                    'onset_s':None if role=='NORMAL' else 2,
                    'metadata_not_model_features':True}
            archive.writestr(f'targets/{token}.json',json.dumps(target))
            csv_gz(src,f'{token}.csv.gz',fault=role=='FAULT',unlabeled=True)
    plan,report=build_plan(src,target_zip,model='TEST-family')
    assert len(plan['files'])==2
    assert len(report['excluded_train_fault'])==1
    train=next(x['config'] for x in plan['files'] if x['config']['role']=='train')
    heldout=next(x['config'] for x in plan['files'] if x['config']['role']=='test')
    assert train['all_normal_confirmed'] is False
    assert heldout['import_context']['dataset_split']=='validation'
    assert heldout['import_context']['target_metadata']['role']=='FAULT'
    assert train['time_reference']!=heldout['time_reference']


def test_manifest_cli_dry_run_and_report(tmp_path):
    import subprocess
    import sys
    db=database(tmp_path)
    source=csv_gz(tmp_path,'sample.csv.gz')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':[{'path':source.name,'config':config()}]}))
    report=tmp_path/'report.json'
    r=subprocess.run([sys.executable,'-m','scripts.batch_import_v2','--database',str(db),
       '--manifest',str(manifest),'--dry-run','--report',str(report)],
       capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    assert json.loads(report.read_text(encoding='utf-8'))['counts']['preview']==1
    assert counts(db)['v2_sources']==0


def test_insert_failure_rolls_back_source_segments_samples_and_receipt(tmp_path):
    db=database(tmp_path);file=csv_gz(tmp_path,'crash.csv.gz',rows=30)
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TRIGGER reject_mid_insert BEFORE INSERT ON v2_samples
                        WHEN NEW.source_ordinal >= 4
                        BEGIN SELECT RAISE(ABORT,'synthetic_disk_failure'); END''')
    with pytest.raises(sqlite3.IntegrityError,match='synthetic_disk_failure'):
        import_file(db,file,config())
    assert counts(db)=={'v2_sources':0,'v2_segments':0,'v2_samples':0}
    with sqlite3.connect(db) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_batch_import_receipts'").fetchone() or conn.execute('SELECT COUNT(*) FROM v2_batch_import_receipts').fetchone()[0]==0
        conn.execute('DROP TRIGGER reject_mid_insert')
    assert import_file(db,file,config())['status']=='imported'
    assert counts(db)['v2_sources']==1


def test_normal_unlabeled_training_requires_explicit_review(tmp_path):
    db=database(tmp_path);file=csv_gz(tmp_path,unlabeled=True)
    setup={**config(),'role':'train','unlabeled_test_confirmed':False}
    with pytest.raises(ValueError,match='确认全部正常'):
        import_file(db,file,setup)
    assert counts(db)['v2_sources']==0
    assert import_file(db,file,{**setup,'all_normal_confirmed':True})['status']=='imported'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int=0').fetchone()[0]==8
