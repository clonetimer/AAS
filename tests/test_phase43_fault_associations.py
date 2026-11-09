"""Phase 43: source-level truth must never become every channel's fault verdict."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import urlparse

import pytest

from scripts.annotate_uploaded_targets_v2 import SCHEMA as RUN_SCHEMA
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.telemetry_v2_governance import create_event, list_events
from scripts.server.telemetry_v2_fault_associations import fault_associations
from scripts.server.data_routes import GET_DATA_ROUTES


def fixture_db(tmp_path):
    folder = tmp_path / 'datasets'; folder.mkdir()
    path = folder / 'aas_telemetry_v2.sqlite'
    with sqlite3.connect(path) as conn:
        create_v2(conn)
    raw=['timestamp,label,st,gyro,rw,flat']
    for t in range(120):
        # All four channels share the same run-level (not channel-level) label.
        baseline=(t % 6 - 2.5) / 5
        after = t >= 60
        raw.append(f'{t},{int(after)},{baseline + (10 if after else 0)},'
                   f'{baseline + (3 if after else 0)},{baseline},5')
    mapping={
        'st':{'subsystem':'adcs','device':'star_tracker','variable':'q0'},
        'gyro':{'subsystem':'adcs','device':'gyroscope','variable':'rate'},
        'rw':{'subsystem':'adcs','device':'reaction_wheel','variable':'measured_speed'},
        'flat':{'subsystem':'adcs','device':'thermal_monitor','variable':'temperature'},
    }
    payload=dict(namespace='mission_14', model='SIM-SAT-014', satellite_instance='',
        subsystem='adcs', device='generic', role='test', unit='1', time_domain='numeric_unspecified',
        time_reference='run_14_elapsed',time_unit='s',filename='run14.csv',
        column_metadata=mapping,csv='\n'.join(raw)+'\n')
    imported=import_wide_csv(path,payload,commit=True)
    source_id=imported['source_id']
    with sqlite3.connect(path) as conn:
        conn.executescript(RUN_SCHEMA)
        conn.execute("UPDATE v2_segments SET role='simulation' WHERE source_id=?",(source_id,))
        conn.execute('''INSERT INTO v2_run_annotations(source_id,token,model_alias,model_identity_kind,
            split,group_id,run_role,fault_type,fault_component,onset_s,fault_end_exclusive_s,
            normal_frames,fault_frames,annotation_scope,metadata_not_model_features,
            target_sha256,target_archive_sha256,provenance)
            VALUES (?,?,?,'synthetic_alias','validation','group14','FAULT',
                    'star_tracker_bias','star_tracker',60,120,60,60,'simulation_run',1,'hash','hash','test')''',
            (source_id,'run14','SIM-SAT-014'))
        conn.execute('''CREATE TABLE v2_run_fault_locations(
            source_id INTEGER PRIMARY KEY, subsystem TEXT NOT NULL, device TEXT NOT NULL,
            classification_basis TEXT NOT NULL, note TEXT NOT NULL)''')
        conn.execute("INSERT INTO v2_run_fault_locations VALUES (?,'adcs','star_tracker','manual','source confirmed')",(source_id,))
        conn.commit()
    return path, {row['source_column']:row['channel_id'] for row in imported['channels']}


def test_affected_not_equivalent_to_fault_origin_and_run_labels_are_read_only(tmp_path):
    db, ids=fixture_db(tmp_path)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    data=fault_associations(db,model='SIM-SAT-014')
    assert data['count']==4
    entries={item['channel']['id']:item for item in data['items']}
    assert entries[ids['st']]['category']=='source'
    assert entries[ids['st']]['origin_verified'] is True
    assert entries[ids['gyro']]['category']=='affected'
    assert entries[ids['gyro']]['origin_verified'] is False
    assert entries[ids['rw']]['category']=='no_clear_change'
    assert entries[ids['rw']]['origin_verified'] is False
    assert entries[ids['flat']]['category']=='insufficient'
    assert entries[ids['rw']]['stats']['fault_count']==60
    assert len(data['events'])==1 and data['events'][0]['onset_s']==60
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
    # Foreign component run event is metadata, never a fault rectangle on RW.
    events=list_events(db,channel_id=ids['rw'],role='test')
    assert events['items']==[]
    assert events['run_annotations'][0]['annotation_scope']=='simulation_run'


def test_exact_channel_event_visible_without_changing_other_channel(tmp_path):
    db, ids=fixture_db(tmp_path)
    # Test an authorized channel-scoped event created for the test role,
    # then restore the simulation-run view without altering the event itself.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE v2_segments SET role='test' WHERE channel_id=?",(ids['gyro'],))
    create_event(db,{'channel_id':ids['gyro'],'role':'test','start':'60',
                     'end':'119','label':1,'fault_type':'gyro_fault','confirm':True})
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE v2_segments SET role='simulation' WHERE channel_id=?",(ids['gyro'],))
    out={item['channel']['id']:item for item in fault_associations(db,model='SIM-SAT-014')['items']}
    assert out[ids['gyro']]['category']=='source' and out[ids['gyro']]['channel_event']
    assert out[ids['gyro']]['origin_verified'] is False
    assert out[ids['rw']]['category']=='no_clear_change'
    assert len(list_events(db,channel_id=ids['gyro'],role='test')['items'])==1
    assert not list_events(db,channel_id=ids['rw'],role='test')['items']


def test_missing_or_ambiguous_run_evidence_does_not_claim_anomaly(tmp_path):
    db, ids=fixture_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('DROP TABLE v2_run_fault_locations')
        conn.execute('DELETE FROM v2_run_annotations')
    out=fault_associations(db,model='SIM-SAT-014')
    assert all(i['category']=='insufficient' for i in out['items'])
    assert all(not i['origin_verified'] for i in out['items'])
    with pytest.raises(ValueError,match='选择一个卫星型号'):
        fault_associations(db,model='')
    with pytest.raises(ValueError,match='仅使用测试'):
        fault_associations(db,model='SIM-SAT-014',role='train')



def test_same_model_different_source_cannot_inherit_origin(tmp_path):
    from scripts.server.telemetry_v2_import import import_csv
    db, _=fixture_db(tmp_path)
    independent=import_csv(db,dict(namespace='mission_14',model='SIM-SAT-014',
        satellite_instance='',subsystem='adcs',device='star_tracker',variable='other_q',
        unit='1',role='test',time_domain='numeric_unspecified',time_reference='independent_clock',
        time_unit='s',filename='independent.csv',csv='timestamp,value,label\n0,1,0\n1,2,1\n'),commit=True)
    data=fault_associations(db,model='SIM-SAT-014')
    entry=next(i for i in data['items'] if i['channel']['id']==independent['channel_id'])
    assert not entry['origin_verified']
    assert entry['source_id'] is None
    assert entry['first_fault_timestamp'] is None
    assert entry['category']=='insufficient'


def test_http_contract_and_frontend_semantic_layout(tmp_path):
    db, _=fixture_db(tmp_path)
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,result,status=200):return status,result
    url=urlparse('/api/v2/telemetry/fault-associations?database=aas_telemetry_v2.sqlite&model=SIM-SAT-014')
    code,response=GET_DATA_ROUTES[url.path](None,url,Main)
    assert code==200 and response['count']==4
    root=Path(__file__).resolve().parents[1]/'web'/'visualization'
    html=(root/'browser.html').read_text('utf-8')
    js=(root/'telemetry-v2-browser.js').read_text('utf-8')
    assert html.count('class="v2-layout"')==1
    assert 'id="v2FindAssociations"' in html
    assert 'id="v2AssociationClass"' in html
    assert '/api/v2/telemetry/fault-associations' in js
    assert "point.label===1?'#d84a4a':color" not in js
    assert "inChannelEvent?'#d84a4a':color" in js
    p=subprocess.run(['node','--check',str(root/'telemetry-v2-browser.js')],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
