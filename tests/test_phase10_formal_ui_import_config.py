"""Phase10 regression: simple formal UI, native wide import and run split views."""
from pathlib import Path
import sqlite3
import pytest
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.telemetry_v2_access import list_channels, query_charts
from scripts.server.v2_database_config import active_name,active_path

HERE=Path(__file__).parents[1]

def db(tmp_path):
    path=tmp_path/'custom_satellite.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    return path

def wide(**kw):
    result=dict(filename='external.csv',namespace='user_data',model='GF1',subsystem='ADCS',
                device='RW1',unit='rpm',role='train',time_domain='numeric_unspecified',
                time_reference='run_A_elapsed',time_unit='s',
                csv='time_s,speed,current,label\n0,120,0.5,0\n1,121,0.55,0\n')
    return {**result,**kw}

def count(path):
    with sqlite3.connect(path) as conn:
        return tuple(conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
            ('v2_sources','v2_channels','v2_segments','v2_samples'))

def test_preview_atomic_write_and_query(tmp_path):
    path=db(tmp_path)
    old=count(path)
    result=import_wide_csv(path,wide())
    assert result['column_count']==2 and count(path)==old
    done=import_wide_csv(path,wide(),commit=True)
    assert count(path)==(1,2,2,4)
    series=list_channels(path,role='train')['items']
    assert {s['variable'] for s in series}=={'speed','current'}
    assert query_charts(path,role='train',channel_ids=[series[0]['id']])['series'][0]['matched_count']==2
    assert done['source_id']>0

def test_rejects_any_fault_in_train_and_rollback(tmp_path):
    path=db(tmp_path)
    with pytest.raises(ValueError,match='故障标签'):
        import_wide_csv(path,wide(csv='time_s,speed,current,label\n0,120,0.5,0\n1,121,0.6,1\n'),commit=True)
    assert count(path)==(0,0,0,0)

def test_undeclared_missing_labels_rejected(tmp_path):
    path=db(tmp_path)
    without='time_s,speed,current\n0,120,0.5\n1,121,0.55\n'
    with pytest.raises(ValueError,match='真实标签'):
        import_wide_csv(path,wide(csv=without),commit=True)
    ok=import_wide_csv(path,wide(csv=without,all_normal_confirmed=True),commit=True)
    assert ok['row_count']==2

def test_entire_table_conflict_rejected(tmp_path):
    path=db(tmp_path)
    import_wide_csv(path,wide(),commit=True)
    before=count(path)
    with pytest.raises(ValueError,match='重叠时间戳'):
        import_wide_csv(path,wide(csv='time_s,speed,current,label\n1,300,0.66,0\n'),commit=True)
    assert count(path)==before

def test_column_mapping_and_test_labeled_fault(tmp_path):
    path=db(tmp_path)
    r=import_wide_csv(path,wide(role='test',csv='time_s,adcs.gyro.bias_x,label\n0,0.5,0\n1,3.1,1\n',
                  column_metadata={'adcs.gyro.bias_x':{'unit':'rad/s','device':'gyro'}}),commit=True)
    items=list_channels(path,role='test')['items']
    assert len(items)==1 and items[0]['device']=='gyro' and items[0]['variable']=='bias_x'
    assert [p['label'] for p in query_charts(path,role='test',channel_ids=[items[0]['id']])['series'][0]['points']]==[0,1]

def test_configurable_name(monkeypatch,tmp_path):
    monkeypatch.setenv('AAS_V2_DB_FILENAME','my_mission.sqlite')
    assert active_name()=='my_mission.sqlite'
    assert active_path(tmp_path)==(tmp_path/'datasets'/'my_mission.sqlite').resolve()
    for value in ('../secret.db','/tmp/a.db','other.csv'):
        monkeypatch.setenv('AAS_V2_DB_FILENAME',value)
        with pytest.raises(ValueError):active_name()

def test_no_developer_panels_and_only_two_roles():
    web=HERE/'web'/'visualization'
    html=(web/'browser.html').read_text(encoding='utf-8')
    assert all(x not in html for x in ('v2ImportPanel','v2GovernancePanel','v2PreprocessPanel','v2Satellite'))
    assert html.count('value="train"')==1 and html.count('value="test"')==1
    assert all((web/f'{name}.html').is_file() for name in ('import','preprocess'))
    assert not (web/'governance.html').exists()  # Phase 14 removed this end-user page

def test_external_wide_csv_is_trainable_as_logical_runs(tmp_path):
    from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection
    path=db(tmp_path)
    t1=import_wide_csv(path,wide(time_reference='normal_A'),commit=True)
    t2=import_wide_csv(path,wide(namespace='user_data_B',time_reference='normal_B',csv='time_s,speed,current,label\n0,130,0.6,0\n1,132,0.65,0\n'),commit=True)
    heldout=import_wide_csv(path,wide(namespace='user_data_C',role='test',time_reference='test_C',
        csv='time_s,speed,current,label\n0,124,0.51,0\n1,300,1.25,1\n'),commit=True)
    found=catalog(path)
    assert len(found['default_train_ids'])==2
    assert len(found['default_test_ids'])==1
    assert {x['feature_key'] for x in found['features']}=={'ADCS.RW1.speed','ADCS.RW1.current'}
    features=['ADCS.RW1.speed','ADCS.RW1.current']
    selection={'source_ids':[t1['source_id'],t2['source_id']],'feature_keys':features,
               'start':0,'end':1,'method':'exact','tolerance_seconds':0}
    expected=validate_training_selection(path,{'dataset_selection':selection,'train_ratio':0.5})
    assert expected['aligned_count']==4 and expected['fault_count']==0
    test_sel={**selection,'source_ids':[heldout['source_id']]}
    rows,meta=load_selection(path,selection=test_sel,role='test',expected={
        'feature_keys':features,'train_source_ids':selection['source_ids'],
        'train_groups':[r['group_id'] for r in expected['runs']]})
    assert len(rows)==2 and meta['fault_count']==1


def test_same_clock_cannot_leak_between_imported_train_and_test(tmp_path):
    from scripts.server.v2_run_dataset import load_selection,validate_training_selection
    path=db(tmp_path)
    a=import_wide_csv(path,wide(time_reference='shared_run'),commit=True)
    b=import_wide_csv(path,wide(namespace='user_data_B',time_reference='new_run'),commit=True)
    c=import_wide_csv(path,wide(role='test',time_reference='shared_run',
        csv='time_s,speed,current,label\n2,300,1.25,1\n'),commit=True)
    sel={'source_ids':[a['source_id'],b['source_id']],'feature_keys':['ADCS.RW1.speed','ADCS.RW1.current'],
         'start':None,'end':None,'method':'exact','tolerance_seconds':0}
    contract=validate_training_selection(path,{'dataset_selection':sel,'train_ratio':0.5})
    with pytest.raises(ValueError,match='仿真组|泄漏'):
        load_selection(path,selection={**sel,'source_ids':[c['source_id']]},role='test',expected={
            'feature_keys':sel['feature_keys'],'train_source_ids':sel['source_ids'],
            'train_groups':[r['group_id'] for r in contract['runs']]})
