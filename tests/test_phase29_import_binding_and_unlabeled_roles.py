"""Phase29: explicit role and per-column identity, unknown unlabeled test labels."""
import sqlite3
from pathlib import Path
import pytest
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.telemetry_v2_access import list_channels,query_charts


def fixture(tmp_path):
    db=tmp_path/'satellite.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
    return db


def payload(**overrides):
    cfg=dict(filename='telemetry.csv',namespace='mission_001',model='GF1',subsystem='ADCS',device='RW1',
        unit='rad/s',role='test',time_domain='numeric_unspecified',time_reference='mission_001',time_unit='s',
        csv='time_s,speed,current\n0,10,0.2\n1,12,0.3\n',unlabeled_test_confirmed=True,
        require_explicit_mapping=True,column_metadata={
            'speed':{'subsystem':'ADCS','device':'RW1','variable':'speed','unit':'rad/s'},
            'current':{'subsystem':'EPS','device':'PDU','variable':'current','unit':'A'},
        })
    return {**cfg,**overrides}


def test_unlabeled_test_requires_explicit_acknowledgment(tmp_path):
    db=fixture(tmp_path)
    with pytest.raises(ValueError,match='未知标签'):
        import_wide_csv(db,payload(unlabeled_test_confirmed=False),commit=True)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0


def test_unlabeled_test_preserves_sql_null_and_binding(tmp_path):
    db=fixture(tmp_path)
    before=db.stat().st_size
    preview=import_wide_csv(db,payload())
    assert preview['label_status']=='unknown'
    assert preview['column_count']==2
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0
    committed=import_wide_csv(db,payload(),commit=True)
    assert committed['status']=='imported'
    with sqlite3.connect(db) as conn:
        channels=conn.execute('SELECT model,namespace,subsystem,device,variable,unit FROM v2_channels ORDER BY variable').fetchall()
        assert channels==[
            ('GF1','mission_001','EPS','PDU','current','A'),
            ('GF1','mission_001','ADCS','RW1','speed','rad/s')]
        assert conn.execute('SELECT COUNT(*), COUNT(label_int) FROM v2_samples').fetchone()==(4,0)
    items=list_channels(db,role='test')['items']
    assert len(items)==2
    curves=query_charts(db,role='test',channel_ids=[items[0]['id']])
    assert len(curves['series'][0]['points'])==2
    assert all(p['label'] is None for p in curves['series'][0]['points'])


def test_user_must_bind_each_csv_column(tmp_path):
    db=fixture(tmp_path)
    partial={'speed':payload()['column_metadata']['speed']}
    with pytest.raises(ValueError,match='列映射不完整'):
        import_wide_csv(db,payload(column_metadata=partial),commit=True)


def test_unlabeled_train_requires_confirmed_normal_and_stays_isolated(tmp_path):
    db=fixture(tmp_path)
    cfg=payload(namespace='train_002',role='train',time_reference='train_002',unlabeled_test_confirmed=False)
    with pytest.raises(ValueError,match='正常'):
        import_wide_csv(db,cfg,commit=True)
    done=import_wide_csv(db,{**cfg,'all_normal_confirmed':True},commit=True)
    assert done['label_status']=='confirmed_normal'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT DISTINCT label_int FROM v2_samples').fetchall()==[(0,)]


def test_test_labels_provided_unchanged(tmp_path):
    db=fixture(tmp_path)
    config=payload(csv='time_s,speed,current,label\n0,10,0.2,0\n1,12,0.3,1\n',unlabeled_test_confirmed=False)
    ret=import_wide_csv(db,config,commit=True)
    assert ret['label_status']=='provided'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT DISTINCT label_int FROM v2_samples ORDER BY label_int').fetchall()==[(0,),(1,)]
