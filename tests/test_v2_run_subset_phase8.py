"""V2 labeled-run subsets: column/time selection, target grouping, clock isolation."""
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.annotate_uploaded_targets_v2 import SCHEMA
from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection


@pytest.fixture
def labeled(tmp_path):
    db=tmp_path/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as c:
        create_v2(c)
        c.executescript(SCHEMA)
        c.execute('''CREATE TABLE v2_segment_clock(segment_id INTEGER PRIMARY KEY,
                reference TEXT NOT NULL, unit TEXT NOT NULL, declared_by TEXT)''')
        for rid,split,run_role in [(1,'train','NORMAL'),(2,'train','NORMAL'),
                                   (3,'validation','NORMAL'),(4,'validation','FAULT'),
                                   (5,'train','FAULT')]:
            namespace=f'run_{rid}'
            c.execute('''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
                    imported_utc,source_table_count,source_row_count) VALUES (?,?,?,?,?,?,?,?)''',
                     (rid,namespace,f'run{rid}.csv.gz',f'hash{rid}',30,'2026-01-01Z',2,6))
            c.execute('''INSERT INTO v2_run_annotations(source_id,token,model_alias,model_identity_kind,
                    split,group_id,run_role,onset_s,normal_frames,fault_frames,annotation_scope,
                    metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
                    VALUES (?,?,?,'synthetic_alias',?,?,?,?,?,?,'simulation_run',1,?,?,?)''',
                    (rid,f'run{rid}',f'SIM-SAT-{rid:03d}',split,f'group{rid}',run_role,
                     4 if run_role=='FAULT' else None,4 if run_role=='FAULT' else 6,
                     2 if run_role=='FAULT' else 0,f'target_hash_{rid}','ziphash','test fixture'))
            for fidx,key in enumerate(['adcs.rw.speed_rad_s','adcs.rw.current_a']):
                cid=(rid-1)*2+fidx+1
                c.execute("INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit) VALUES(?,?,?,?,?,?,?)",
                    (cid,namespace,f'SIM-SAT-{rid:03d}','adcs','reaction_wheel',key.rsplit('.',1)[-1], 'rad/s' if fidx==0 else 'A'))
                c.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
                    role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count)
                    VALUES (?,?,?,?,'telemetry','simulation','source_verified','numeric_unspecified',1,'[]',6)''',
                    (cid,rid,key,cid))
                c.execute('INSERT INTO v2_segment_clock VALUES(?,?,?,?)',
                          (cid,f'elapsed_run:run{rid}','s','test'))
                for t in range(6):
                    label=1 if run_role=='FAULT' and t>=4 else 0
                    c.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
                        time_domain,time_sort_num,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (cid,cid,t,str(t),'numeric_unspecified',float(t),rid*10+t+fidx,label,
                          str(label),str(label),'{}'))
    return db


def choose(ids,keys=None,start=None,end=None,method='exact',tolerance=0):
    return {'source_ids':ids,'feature_keys':keys or ['adcs.rw.speed_rad_s','adcs.rw.current_a'],
            'start':start,'end':end,'method':method,'tolerance_seconds':tolerance}


def test_catalog_and_time_range_feature_order(labeled):
    found=catalog(labeled)
    assert found['default_train_ids']==[1,2]
    assert found['default_test_ids']==[3,4]
    assert len(found['features'])==2
    rows, meta=load_selection(labeled,selection=choose([1,2],start=2,end=4),role='train')
    assert len(rows)==6 and {r['label'] for r in rows}=={0}
    # Training order is canonical (lexicographic), not the caller's click order.
    assert meta['feature_keys']==['adcs.rw.current_a','adcs.rw.speed_rad_s']
    assert rows[0]['value']==13 and rows[0]['source_2']==12
    assert {r['run_source_id'] for r in rows}=={1,2}
    assert len({r['timestamp'] for r in rows})==len(rows)  # equal time_s never merges runs


def test_heldout_normal_and_fault_and_narrow_fault_window(labeled):
    selection=choose([3,4],start=3,end=5)
    rows,meta=load_selection(labeled,selection=selection,role='test',expected={
        'feature_keys':selection['feature_keys'],'train_source_ids':[1,2],
        'train_groups':['group1','group2']})
    assert len(rows)==6 and meta['normal_count']==4 and meta['fault_count']==2
    just_fault, m=load_selection(labeled,selection=choose([4],start=4,end=5),role='test')
    assert len(just_fault)==2 and m['normal_count']==0 and m['fault_count']==2


def test_leakage_and_fault_training_rejected(labeled):
    with pytest.raises(ValueError,match='划分'):
        load_selection(labeled,selection=choose([5]),role='train')
    with pytest.raises(ValueError,match='划分'):
        load_selection(labeled,selection=choose([1]),role='test')
    with pytest.raises(ValueError,match='参量字段|特征'):
        load_selection(labeled,selection=choose([3],keys=['adcs.rw.current_a']),role='test',expected={'feature_keys':['adcs.rw.speed_rad_s']})
    # Synthetic fixture has only six samples, too short for a temporal embargo.
    with pytest.raises(ValueError,match='数据不足|样本不足'):
        validate_training_selection(labeled,{'dataset_selection':choose([1]),'train_ratio':.8})


def test_invalid_time_and_same_run_group_rejected(labeled):
    with pytest.raises(ValueError,match='起点晚于终点'):
        load_selection(labeled,selection=choose([1],start=5,end=4),role='train')
    with pytest.raises(ValueError,match='独立|仿真组|泄漏'):
        load_selection(labeled,selection=choose([3],start=0,end=5),role='test',expected={
             'feature_keys':['adcs.rw.speed_rad_s','adcs.rw.current_a'],
             'train_source_ids':[1,2],'train_groups':['group3']})


def test_clock_must_be_seconds_and_consistent(labeled):
    with sqlite3.connect(labeled) as c:
        c.execute("UPDATE v2_segment_clock SET unit='ms' WHERE segment_id=2")
    with pytest.raises(ValueError,match='时间基准|不同数字'):
        load_selection(labeled,selection=choose([1]),role='train')


def test_ui_uses_runs_features_time_not_tables():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/runner.html').read_text(encoding='utf-8')
    js=(root/'web/visualization/runner-v2.js').read_text(encoding='utf-8')
    assert 'featureColumns' in html and 'trainRuns' in html and 'trainStart' in html
    assert '/api/v2/run-dataset/catalog' in js
    assert '/api/v2/run-dataset/preview' in js
    assert 'dataset_selection' in js and 'algorithm_params' in js
    assert '/api/sqlite-tables' not in js and 'tableSelect' not in html


def test_evaluated_model_still_listed():
    """A scoring call changes model status to evaluated; do not hide it."""
    from scripts.server.telemetry_v2_routes import v2_models

    class Main:
        @staticmethod
        def get_models(*,status,limit):
            return [{'id':7,'file_path':'models/mock','status':'evaluated',
                     'model_name':'mock'}] if status=='evaluated' else []
        @staticmethod
        def resolve_managed_model_dir(_):return 'models/mock'
        @staticmethod
        def load_model_config(_):
            return {'config':{'data_contract':{'version':7,
                'v2_run_selection':{'feature_keys':['adcs.rw.speed_rad_s'],'source_ids':[1,2],
                                    'method':'previous','tolerance_seconds':0.5},
                'v2_run_manifest':{'runs':[{'group_id':'g1'},{'group_id':'g2'}]}}}}

    got=v2_models(Main)
    assert got['total']==1
    assert got['models'][0]['alignment_method']=='previous'
    assert got['models'][0]['tolerance_seconds']==.5
