"""Phase 47: source-scoped metadata, auto-binding and atomic preflight tests."""
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.v2_run_dataset import catalog


def database(tmp_path):
    path=tmp_path/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    return path


def target(token='a'*24, group='b'*24, split='validation', role='FAULT'):
    return {'token':token,'split':split,'group':group,'role':role,
        'fault':'rw_degradation' if role=='FAULT' else None,
        'component':'reaction_wheel' if role=='FAULT' else None,
        'onset_s':2.0 if role=='FAULT' else None,
        'metadata_not_model_features':True}


def payload(*, token='a'*24, group='b'*24, split='validation', run_role='FAULT', namespace=None, csv=None):
    evidence=target(token,group,split,run_role)
    return {'namespace':namespace or token,'model':'SIM-SAT-A','subsystem':'ADCS',
            'device':'reaction_wheel','variable':'placeholder','unit':'rad/s',
            'role':'test' if split!='train' else 'train',
            'time_domain':'numeric_unspecified','time_reference':'elapsed:'+token,
            'time_unit':'s','filename':token+'.csv',
            'csv':csv or ('time_s,adcs.rw.speed,label\n0,1,0\n1,,0\n2,7,1\n5,8,1\n'),
            'column_metadata':{'adcs.rw.speed':{'subsystem':'ADCS','device':'reaction_wheel',
                                              'variable':'speed','unit':'rad/s'}},
            'require_explicit_mapping':True,
            'import_context':{'origin_type':'simulation','label_scope':'run',
                'run_token':token,'group_id':group,'dataset_split':split,'run_role':run_role,
                'target_metadata':evidence}}


def counts(db):
    with sqlite3.connect(db) as c:
        return tuple(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
                     ('v2_sources','v2_segments','v2_samples'))


def test_preview_quality_is_readonly_and_commit_is_atomic(tmp_path):
    db=database(tmp_path)
    p=payload()
    before=counts(db)
    preview=import_wide_csv(db,p)
    assert preview['status']=='preview' and counts(db)==before
    assert preview['quality']['missing_by_column']=={'adcs.rw.speed':1}
    assert preview['quality']['label_counts']=={'0':2,'1':2,'unknown':0}
    assert preview['quality']['gaps_gt_3x_median']==0
    assert preview['import_context']['label_scope']=='run'
    result=import_wide_csv(db,p,commit=True)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM v2_import_context').fetchone()[0]==1
        row=c.execute('''SELECT run_token,dataset_split,group_id,run_role,label_scope,evidence_origin,
                           quality_json FROM v2_import_context''').fetchone()
        assert row[:5]==('a'*24,'validation','b'*24,'FAULT','run')
        assert row[5]=='user_supplied_target_member_unverified'
        assert json.loads(row[6])['total_rows']==4
        assert [r[0] for r in c.execute('SELECT label_int FROM v2_samples ORDER BY source_ordinal')]==[0,0,1,1]
    assert result['source_id']>0


def test_group_cross_split_rejected_without_partial_write(tmp_path):
    db=database(tmp_path)
    import_wide_csv(db,payload(),commit=True)
    old=counts(db)
    p=payload(token='c'*24,split='test_confirmation',namespace='c'*24)
    with pytest.raises(ValueError,match='group.*split'):
        import_wide_csv(db,p,commit=True)
    assert counts(db)==old


def test_same_token_conflict_rejected_even_distinct_namespace(tmp_path):
    db=database(tmp_path)
    import_wide_csv(db,payload(),commit=True)
    different=payload(namespace='another_run_namespace',group='d'*24)
    with pytest.raises(ValueError,match='token'):
        import_wide_csv(db,different)


def test_fault_run_cannot_be_train_and_normal_cannot_have_fault_label(tmp_path):
    db=database(tmp_path)
    p=payload(split='train',run_role='FAULT')
    with pytest.raises(ValueError,match='故障运行|训练数据中存在故障标签'):
        import_wide_csv(db,p)
    p=payload(split='train',run_role='NORMAL')
    with pytest.raises(ValueError,match='NORMAL|训练数据中存在故障标签'):
        import_wide_csv(db,p)
    assert counts(db)==(0,0,0)


def test_claimed_parameter_truth_rejected_and_target_conflict(tmp_path):
    db=database(tmp_path)
    p=payload()
    p['import_context']['label_scope']='parameter'
    with pytest.raises(ValueError,match='targets 标签仅证明运行级'):
        import_wide_csv(db,p)
    p=payload();p['import_context']['run_token']='e'*24
    with pytest.raises(ValueError,match='冲突'):
        import_wide_csv(db,p)


def test_context_is_optional_for_legacy_import(tmp_path):
    db=database(tmp_path)
    p=payload();p.pop('import_context')
    result=import_wide_csv(db,p,commit=True)
    assert result['import_context']=={}
    assert counts(db)==(1,1,4)
    with sqlite3.connect(db) as c:
        tables=[x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE name='v2_import_context'")]
        assert tables==[]


def test_run_catalog_uses_actual_split_and_group_not_role_alias(tmp_path):
    db=database(tmp_path)
    import_wide_csv(db,payload(),commit=True)
    runs=[r for r in catalog(db)['runs'] if r['data_role']=='test']
    assert len(runs)==1 and runs[0]['split']=='validation'
    assert runs[0]['group_id']=='b'*24
    assert runs[0]['label_scope']=='run'
    assert runs[0]['evidence_origin']=='user_supplied_target_member_unverified'


def test_known_column_catalog_matches_56_without_inventing_units():
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'web/visualization/telemetry-v2-column-catalog.json').read_text('utf-8'))
    assert len(data['items'])==56
    assert data['items']['adcs.rw.measured_speed_rad_s_0']['unit']=='rad/s'
    assert data['items']['eps.pdu.payload_enabled']['unit']==''
    html=(root/'web/visualization/import.html').read_text('utf-8')
    js=root/'web/visualization/telemetry-v2-import.js'
    script=js.read_text('utf-8')
    for name in ['v2ImpRunToken','v2ImpDatasetSplit','v2ImpGroup','v2ImpTargetJson','v2ImpOrigin','v2ImpLabelScope']:
        assert 'id="'+name+'"' in html and "'"+name+"'" in script
    assert 'mappingFromAuto' in script and 'telemetry-v2-column-catalog.json' in script
    assert '<input id="v2ImpMapFile" type="file" accept=".csv,text/csv" />' in html
    if shutil.which('node'):
        c=subprocess.run(['node','--check',str(js)],capture_output=True,text=True)
        assert c.returncode==0,c.stderr


def test_imported_heldout_split_is_selected_without_role_alias(tmp_path):
    from scripts.server.v2_run_dataset import _selected
    db=database(tmp_path)
    result=import_wide_csv(db,payload(split='test_confirmation'),commit=True)
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        selected=_selected(c,[result['source_id']],'test')
    assert selected[0]['split']=='test_confirmation'
    assert selected[0]['group_id']=='b'*24


def test_same_token_changed_model_is_rejected(tmp_path):
    db=database(tmp_path)
    import_wide_csv(db,payload(),commit=True)
    p=payload(namespace='another')
    with pytest.raises(ValueError,match='token'):
        import_wide_csv(db,p)


def test_gzip_metadata_uses_original_sha_and_context(tmp_path):
    import gzip,hashlib
    from scripts.server.telemetry_v2_source_import import source_operation
    db=database(tmp_path)
    raw=gzip.compress(payload()['csv'].encode('utf-8'))
    p=payload();p.pop('csv');p['filename']='a'*24+'.csv.gz'
    meta={'operation':'preview','filename':p['filename'],'csv_encoding':'utf-8',
          'csv_delimiter':',','format_confirmed':True,'config':p}
    preview=source_operation(db,raw,meta)
    assert preview['import_context']['dataset_split']=='validation'
    assert preview['sha256']==hashlib.sha256(raw).hexdigest()
    assert counts(db)==(0,0,0)
    done=source_operation(db,raw,{**meta,'operation':'commit'})
    assert done['source_id']>0


def test_import_history_exposes_context_and_quality(tmp_path):
    from scripts.server.telemetry_v2_import import list_imports
    db=database(tmp_path)
    import_wide_csv(db,payload(),commit=True)
    history=list_imports(db)['items']
    assert len(history)==1
    assert history[0]['run_token']=='a'*24
    assert history[0]['dataset_split']=='validation'
    assert history[0]['quality']['missing_by_column']['adcs.rw.speed']==1
