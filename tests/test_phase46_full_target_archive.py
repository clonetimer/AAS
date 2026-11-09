"""Phase46 targets.zip evidence, split hygiene, source boundary and UI contract."""
from pathlib import Path
from zipfile import ZipFile
import warnings
import json
import sqlite3
import pytest

from scripts.target_archive_evidence import read_targets, summarize, check_annotated_database, HELDOUT_SPLITS
from scripts.target_fault_location import FAULT_LOCATIONS
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.annotate_uploaded_targets_v2 import SCHEMA
from scripts.server.v2_run_dataset import catalog,load_selection
from tests.test_v2_run_subset_phase8 import labeled,choose


def make_zip(path, items):
    with ZipFile(path,'w') as z:
        for i, item in enumerate(items):
            token=item['token']
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                z.writestr('targets/'+token+'.json',json.dumps(item,ensure_ascii=False).encode('utf-8'))
    return path


def item(token, group, split='train', role='NORMAL'):
    return {'token':token,'group':group,'split':split,'role':role,
        'fault':'wheel_torque_loss' if role=='FAULT' else None,
        'component':'reaction_wheel' if role=='FAULT' else None,
        'onset_s':3.0 if role=='FAULT' else None,
        'metadata_not_model_features':True}


def test_full_target_archive_splits_and_counts(tmp_path):
    records=read_targets(make_zip(tmp_path/'targets.zip',[
        item('a'*24,'1'*24),item('b'*24,'1'*24,'train','FAULT'),
        item('c'*24,'2'*24,'ood_scene','FAULT')]))
    report=summarize(records)
    assert (report['total'],report['fault'],report['normal'],report['group_count'])==(3,2,1,2)
    assert report['splits']['train']=={'NORMAL':1,'FAULT':1}
    assert 'component' in report['model_features_must_exclude']
    assert 'ood_scene' in HELDOUT_SPLITS


def test_group_cross_split_or_bad_metadata_rejected(tmp_path):
    paths=[[item('a'*24,'1'*24),item('b'*24,'1'*24,'validation','FAULT')],
           [{**item('a'*24,'1'*24),'metadata_not_model_features':False}],
           [item('a'*24,'1'*24),item('a'*24,'2'*24)]]
    for idx, rows in enumerate(paths):
        with pytest.raises(ValueError):read_targets(make_zip(tmp_path/f'bad{idx}.zip',rows))


def test_archive_path_traversal_and_malformed_onset_rejected(tmp_path):
    path=tmp_path/'evil.zip'
    with ZipFile(path,'w') as z:z.writestr('../targets/'+'a'*24+'.json','{}')
    with pytest.raises(ValueError):read_targets(path)
    bad=item('a'*24,'1'*24,'validation','FAULT');bad['onset_s']=float('nan')
    with pytest.raises(ValueError):read_targets(make_zip(tmp_path/'nan.zip',[bad]))


def test_db_member_sha_not_raw_csv_hash(tmp_path):
    token='a'*24
    label=item(token,'1'*24,'validation','FAULT')
    z=make_zip(tmp_path/'targets.zip',[label]);records=read_targets(z)
    db=tmp_path/'runs.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
        conn.executescript(SCHEMA)
        conn.execute('''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count)
         VALUES (1,'run',?,'raw-file-sha-not-in-zip',1,'today',0,0)''', (token+'.csv.gz',))
        conn.execute('''INSERT INTO v2_run_annotations
          (source_id,token,model_alias,model_identity_kind,split,group_id,run_role,fault_type,
           fault_component,onset_s,fault_end_exclusive_s,normal_frames,fault_frames,
           annotation_scope,metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
          VALUES (1,?,'SIM-SAT-001','synthetic_alias','validation',?,'FAULT',
             'wheel_torque_loss','reaction_wheel',3,10,3,7,'simulation_run',1,?,'zipsha','test')''',
             (token,'1'*24,records[token]['target_sha256']))
    valid=check_annotated_database(db,records)
    assert valid['blocked']==0 and valid['matched_archive_tokens']==1
    assert valid['raw_telemetry_hash_verified'] is False
    with sqlite3.connect(db) as conn:conn.execute("UPDATE v2_run_annotations SET target_sha256='tampered'")
    invalid=check_annotated_database(db,records)
    assert invalid['blocked']==1
    assert 'target_sha256_mismatch' in invalid['issues'][0]['errors']


def test_extended_component_source_taxonomy_complete():
    required={'imu.accelerometer','reaction_wheel','reaction_wheel.speed_sensor',
       'sun_sensor','battery','solar_array','propulsion.tank','thermal.radiator',
       'comm.transmitter','comm.downlink','imu'}
    assert required <= FAULT_LOCATIONS.keys()
    assert FAULT_LOCATIONS['magnetometer'][:2]==('adcs','magnetometer')
    assert FAULT_LOCATIONS['reaction_wheel'][:2]==('adcs','reaction_wheel')
    assert FAULT_LOCATIONS['propulsion.tank'][2]=='source_known_no_direct_channel'


def test_ood_and_confirmation_are_separate_heldout_metrics(labeled):
    with sqlite3.connect(labeled) as conn:
        conn.execute("UPDATE v2_run_annotations SET split='ood_scene' WHERE source_id=4")
    c=catalog(labeled)
    assert c['default_test_ids']==[3]
    assert 'ood_scene' in c['heldout_splits']
    assert next(x for x in c['runs'] if x['source_id']==4)['allowed_role']=='test'
    rows,meta=load_selection(labeled,selection=choose([4]),role='test')
    assert len(rows)==6 and meta['evaluation_split']=='ood_scene'
    with pytest.raises(ValueError,match='同一种数据划分'):
        load_selection(labeled,selection=choose([3,4]),role='test')
    with pytest.raises(ValueError,match='划分'):
        load_selection(labeled,selection=choose([5]),role='train')


def test_ui_target_split_selection_in_existing_panel():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/runner.html').read_text('utf8')
    js=(root/'web/visualization/runner-v2.js').read_text('utf8')
    assert 'id="evalTargetSplit"' in html
    for name in ('validation','test_confirmation','ood_scene','ood_joint','ood_severity'):
        assert f'value="{name}"' in html
    assert "$('evalTargetSplit').value" in js
    assert '运行标识（合成别名）' in html


def test_complete_archive_copy_repairs_only_imported_run(tmp_path):
    from scripts.audit_v2_fault_truth import inspect,apply_to_new_copy
    token='d'*24
    label=item(token,'2'*24,'test_confirmation','FAULT')
    archive=make_zip(tmp_path/'targets.zip',[label])
    evidence=read_targets(archive)
    db=tmp_path/'runs.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
        conn.executescript(SCHEMA)
        conn.execute('''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
            imported_utc,source_table_count,source_row_count) VALUES (1,'r',?, 'external_raw_sha',1,'now',0,0)''',
            (token+'.csv.gz',))
        conn.execute('''INSERT INTO v2_run_annotations (source_id,token,model_alias,model_identity_kind,split,
            group_id,run_role,fault_type,fault_component,onset_s,fault_end_exclusive_s,normal_frames,fault_frames,
            annotation_scope,metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
            VALUES (1,?,'SIM-SAT-001','synthetic_alias','test_confirmation',?,'FAULT',
            'wheel_torque_loss','reaction_wheel',3,10,3,7,'simulation_run',1,?,'archive','fixture')''',
            (token,'2'*24,evidence[token]['target_sha256']))
    original=db.read_bytes()
    report=inspect(db,targets_zip=archive)
    assert report['database_runs']==1 and report['needs_install']==1 and report['blocked']==0
    assert report['archive_total']==1 and report['raw_telemetry_hash_verified'] is False
    output=tmp_path/'verified.sqlite'
    verified=apply_to_new_copy(db,output,targets_zip=archive)
    assert verified['needs_install']==0 and db.read_bytes()==original
    with sqlite3.connect(output) as conn:
        assert conn.execute('SELECT device FROM v2_run_fault_locations').fetchone()[0]=='reaction_wheel'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
