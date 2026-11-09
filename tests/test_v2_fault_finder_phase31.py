"""Fault-first navigator: source isolation, annotation semantics and UI contract."""
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib.parse import urlparse

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_fault_finder import fault_index
from scripts.server.telemetry_v2_governance import create_event, retract_event, list_events, set_segment_status
from scripts.server.data_routes import GET_DATA_ROUTES
from scripts.annotate_uploaded_targets_v2 import SCHEMA as RUN_SCHEMA


def upload(db, *, namespace='mission_A', device='RW', variable='speed', role='test',
           data=None, time_reference='clock_A'):
    if data is None:
        data = [(0, 10, 0), (1, 11, 0), (2, 9, 0), (3, 10, 0),
                (4, 10, 0), (5, 35, 1), (6, 38, 1), (7, 39, 1)]
    lines = ['timestamp,value,label'] + [f'{time},{"" if value is None else value},{label}' for time, value, label in data]
    return import_csv(db, dict(namespace=namespace, model='GF1', subsystem='ADCS',
        device=device,variable=variable,unit='rpm',role=role, time_domain='numeric_unspecified',
        time_reference=time_reference,time_unit='s', filename=f'{variable}.csv',csv='\n'.join(lines)+'\n'),commit=True)


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'datasets'/'aas_telemetry_v2.sqlite'
    path.parent.mkdir()
    with sqlite3.connect(path) as conn: create_v2(conn)
    return path


def test_sample_label_fault_index_and_contrast(db):
    faulty=upload(db)
    upload(db,namespace='mission_B',data=[(0,1,0),(1,2,0),(2,1,0)])
    upload(db,namespace='mission_C',role='train',data=[(0,1,0),(1,2,0)])
    view=fault_index(db)
    assert view['count']==1 and view['role']=='test'
    item=view['items'][0]
    assert item['channel']['id']==faulty['channel_id']
    assert item['fault_samples']==3 and item['normal_samples']==5
    assert item['shift_sigma']>5
    assert item['evidence']==['sample_label']
    assert item['is_fault_origin'] is False and item['fault_types']==[]
    assert fault_index(db,filters={'namespace':'mission_B'})['count']==0
    assert fault_index(db,filters={'device':'BOGUS'})['count']==0
    with pytest.raises(ValueError,match='训练'):fault_index(db,role='train')
    with pytest.raises(ValueError):fault_index(db,filters={'not_a_column':'secret'})
    with pytest.raises(ValueError):fault_index(db,limit=2000)


def test_explicit_event_visible_even_without_fault_samples(db):
    inp=upload(db,data=[(0,1,0),(1,2,0),(2,1,0),(3,4,0)])
    assert fault_index(db)['count']==0
    new=create_event(db,{'channel_id':inp['channel_id'],'role':'test','start':'1','end':'3',
                         'label':1,'fault_type':'rw_friction','confirm':True})
    out=fault_index(db)['items'][0]
    assert out['evidence']==['channel_event']
    assert out['fault_types']==['rw_friction']
    assert out['shift_sigma'] is None
    assert out['first_fault_timestamp']=='1'
    retract_event(db,{'event_id':new['event_id'],'expected_revision':0,'reason':'review','confirm':True})
    assert fault_index(db)['count']==0


def test_nonfinite_invalid_and_inactive_sources(db):
    data=[(0,1,0),(1,1,0),(2,1,0),(3,None,1),(4,8,1)]
    inp=upload(db,data=data)
    result=fault_index(db)['items'][0]
    assert result['fault_samples']==1 and result['invalid_fault_samples']==1
    assert result['shift_sigma'] is None  # constant normal baseline is inconclusive
    set_segment_status(db,{'segment_id':inp['segment_id'],'active':False,
                            'expected_revision':0,'reason':'quarantine','confirm':True})
    assert fault_index(db)['count']==0


def test_multiple_fault_types_and_composite_priority(db):
    a=upload(db,namespace='a',device='RW1',variable='speed',data=[(0,1,0),(1,2,0),(2,4,0),(3,8,1),(4,8,1)])
    b=upload(db,namespace='b',device='Gyro',variable='rate',data=[(0,1,0),(1,2,0),(2,3,0),(3,3,1),(4,3,1)])
    create_event(db,{'channel_id':b['channel_id'],'role':'test','start':'3','end':'4',
                     'label':1,'fault_type':'gyro_bias','confirm':True})
    view=fault_index(db)
    assert view['items'][0]['channel']['id']==b['channel_id']
    assert view['fault_types']==['gyro_bias']
    assert fault_index(db,fault_type='gyro_bias')['count']==1
    assert fault_index(db,fault_type='no_such_fault')['count']==0
    assert view['total_candidates']==2
    assert len(fault_index(db,limit=1)['items'])==1
    assert fault_index(db,limit=1)['truncated'] is True


def test_run_level_label_is_not_per_channel_fault_attribution(db):
    inp=upload(db,role='test',data=[(0,1,0),(1,2,0),(2,1,0)])
    with sqlite3.connect(db) as conn:
        conn.executescript(RUN_SCHEMA)
        source_id=conn.execute('SELECT source_id FROM v2_segments WHERE id=?',(inp['segment_id'],)).fetchone()[0]
        conn.execute("UPDATE v2_segments SET role='simulation' WHERE id=?",(inp['segment_id'],))
        conn.execute('''INSERT INTO v2_run_annotations
            (source_id,token,model_alias,model_identity_kind,split,group_id,run_role,
              fault_type,fault_component,onset_s,fault_end_exclusive_s,normal_frames,fault_frames,
              annotation_scope,metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
              source_id,'run01','SIM-SAT-001','synthetic_alias','validation','g001','FAULT',
              'bearing_friction','rw_motor',1,3,1,2,'simulation_run',1,'sha1','sha2','verified'))
        conn.execute('''CREATE TABLE v2_run_fault_locations(
           source_id INTEGER PRIMARY KEY, subsystem TEXT NOT NULL,device TEXT NOT NULL,
           classification_basis TEXT NOT NULL,note TEXT NOT NULL)''')
        conn.execute('INSERT INTO v2_run_fault_locations VALUES (?,?,?,?,?)',
                     (source_id,'ADCS','RW1','manual','fault source known'))
        conn.commit()
    entry=fault_index(db)['items'][0]
    assert entry['evidence']==['run_annotation'] and entry['fault_samples']==0
    assert entry['is_fault_origin'] is False  # actual channel device is RW, not RW1
    assert entry['fault_types']==['bearing_friction']
    payload=list_events(db,channel_id=inp['channel_id'],role='test')
    assert payload['items']==[]
    assert payload['overlay_policy']=='channel_events_only'
    assert payload['run_annotations'] and payload['run_annotations'][0]['annotation_scope']=='simulation_run'
    assert '不代表该遥测参量' in payload['run_annotations'][0]['note']
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE v2_run_annotations SET split='train' WHERE source_id=?",(source_id,))
    assert fault_index(db)['count']==0  # no train run leakage into test
    assert list_events(db,channel_id=inp['channel_id'],role='test')['items']==[]


def test_http_route_exposes_safe_readonly_index(db,tmp_path):
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,payload,status=200):return status,payload
    upload(db)
    route=GET_DATA_ROUTES['/api/v2/telemetry/fault-finder']
    status,out=route(None,urlparse('/api/v2/telemetry/fault-finder?role=test&namespace=mission_A'),Main)
    assert status==200 and out['count']==1
    status,out=route(None,urlparse('/api/v2/telemetry/fault-finder?role=train'),Main)
    assert status==400


def test_fault_finder_ui_contract_and_js_syntax():
    base=Path(__file__).resolve().parents[1]/'web'/'visualization'
    html=(base/'browser.html').read_text('utf-8')
    js=(base/'telemetry-v2-browser.js').read_text('utf-8')
    for field in ('v2FindFaults','v2FaultType','v2FaultResults','v2FaultStatus'):
        assert f'id="{field}"' in html and f"'{field}'" in js
    assert '/api/v2/telemetry/fault-finder' in js
    assert "byId('v2Role').value='test'" in js
    assert "inChannelEvent?'#d84a4a':color" in js
    if (node:=shutil.which('node')):
        p=subprocess.run([node,'--check',str(base/'telemetry-v2-browser.js')],capture_output=True,text=True)
        assert p.returncode==0,p.stderr
