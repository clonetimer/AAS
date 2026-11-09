"""End-to-end source-table fusion contracts: labels, timestamps and models."""
from pathlib import Path
import sqlite3

import pytest
from scripts.core.multi_table_fusion import fuse_sources
from scripts.core.data_loader import DataLoader
from scripts.core.training import TrainingPipeline
from scripts.server import services


def _db(tmp_path):
    db=tmp_path/'sensors.db'
    with sqlite3.connect(db) as conn:
        for name in ('A','B','C'):
            conn.execute(f'CREATE TABLE "{name}"(timestamp TEXT,model TEXT,subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER)')
            conn.executemany(f'INSERT INTO "{name}" VALUES(?,?,?,?,?,?,?)', [
                (f'2026-02-01 00:00:{i:02d}','M','eps','battery',name,float(i+1)*(1 if name=='A' else 10), int(name=='B' and i==7))
                for i in range(12)])
    return db


def test_exact_timestamp_fusion_and_label_provenance(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        rows,meta=fuse_sources(conn,['A','B','C'])
    assert len(rows)==12 and rows[7]['label']==1
    assert rows[7]['source_label_1']==0 and rows[7]['source_label_2']==1
    assert rows[1]['source_2']==20.0 and rows[1]['source_3']==20.0
    assert meta['aligned_count']==12
    assert meta['alignment_policy']=='exact_timestamp_inner_join'


def test_split_chronologically_and_contract_in_pipeline(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE B SET label=0')
    sources=['A','B','C']
    loader=DataLoader(db,limit=0,train_ratio=.75,feature_columns=['value','source_2','source_3'])
    loader.source_tables=sources
    bundle=loader.load_and_split(table='A')
    assert bundle.train_count==9 and bundle.test_count==3
    assert bundle.train_values[0]==[1.0,10.0,10.0]
    assert bundle.metadata['alignment']['coverage']['B']==1.0
    pipeline=TrainingPipeline(db,limit=0,train_ratio=.75)
    result=pipeline.run('three_sigma',table='A',persist_model=False,data_contract={
        'feature_columns':['value','source_2','source_3'],
        'resolved_source_tables':sources,
        'time_selection':{},
        'model_input_scaling':{'enabled':True,'method':'min_max'},
    })
    assert result.data_contract['alignment']['aligned_count']==12
    assert len(result.result_rows)==3
    assert result.data_contract['model_input_scaling']['parameters']['per_feature']['source_2']


def test_duplicate_timestamp_is_rejected(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO B SELECT * FROM B LIMIT 1")
        with pytest.raises(ValueError,match='重复时间戳'):
            fuse_sources(conn,['A','B'])


def test_missing_overlap_and_invalid_label_fail(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE B SET timestamp='2050-01-01 00:00:00'")
        with pytest.raises(ValueError,match='重复时间戳|重叠时间戳不足'):
            fuse_sources(conn,['A','B'])
    db.unlink()
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE B SET label=NULL WHERE timestamp=?',('2026-02-01 00:00:04',))
        with pytest.raises(ValueError,match='label 无效'):
            fuse_sources(conn,['A','B'])


def test_single_table_import_rejects_secondary_numeric_measurements(tmp_path):
    target=f'aas_single_table_rule_{tmp_path.name}.db'
    path=services.PROJECT_ROOT/'datasets'/target
    sqlite3.connect(path).close()
    try:
        mappings={k:{'type':'manual','value':v} for k,v in
                  {'model':'M','subsystem':'eps','device':'battery','variable':'soc'}.items()}
        mappings.update({'timestamp':{'type':'column','value':'timestamp'},
                         'value':{'type':'column','value':'value'},
                         'label':{'type':'column','value':'label'}})
        csv='timestamp,value,extra_sensor,label\n2026-01-01,1,2,0\n2026-01-02,2,3,0\n'
        with pytest.raises(ValueError,match='单表只能有一列测量值'):
            services.import_data(csv,'csv',f'datasets/{target}','', 'one',mappings,data_role='train')
    finally:
        path.unlink(missing_ok=True)


def test_example_train_and_test_groups_are_separate_single_sensor_tables():
    db=Path(__file__).resolve().parents[1]/'datasets/history_sample.db'
    with sqlite3.connect(db) as conn:
        train,meta=fuse_sources(conn,['DEMO_train_temperature','DEMO_train_vibration','DEMO_train_current'])
        test,meta_test=fuse_sources(conn,['DEMO_test_temperature','DEMO_test_vibration','DEMO_test_current'])
    assert len(train)==480 and all(row['label']==0 for row in train)
    assert len(test)==180 and any(row['label']==1 for row in test)
    assert meta['coverage']['DEMO_train_vibration']==1
    assert meta_test['label_policy']=='any_source_fault'


def test_source_identity_stored_for_evaluation_contract(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _,meta=fuse_sources(conn,['A','B'])
    assert meta['source_signatures']['A']=={
        'model':'M','subsystem':'eps','device':'battery','variable':'A'
    }
    assert meta['source_signatures']['B']['variable']=='B'


def test_mixed_sensor_identity_in_one_table_is_rejected(tmp_path):
    db=_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE B SET variable='other_sensor' WHERE timestamp='2026-02-01 00:00:01'")
        with pytest.raises(ValueError,match='多个或不完整'):
            fuse_sources(conn,['A','B'])


def test_tolerance_is_opt_in_and_does_not_reuse_samples(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        # Shifting B by two seconds removes strict overlap at two ends, but
        # nearby one-to-one matches can still be recovered with tolerance.
        conn.execute("UPDATE B SET timestamp = datetime(timestamp, '+2 seconds')")
        strict, strict_meta = fuse_sources(conn, ['A', 'B'])
        near, near_meta = fuse_sources(conn, ['A', 'B'], tolerance_seconds=2.0)
    assert len(strict) == 10
    assert len(near) == 12
    assert near_meta['alignment_policy'] == 'nearest_unique_with_tolerance'
    assert near_meta['tolerance_seconds'] == 2.0
    assert near_meta['offsets']['B']['max_seconds'] <= 2
    assert strict_meta['tolerance_seconds'] == 0
    assert near[0]['value'] == 1.0


def test_tolerance_matches_offset_samples_and_retains_labels(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        # Non-integer offset cannot be recovered by the exact join.
        conn.execute("UPDATE B SET timestamp = strftime('%Y-%m-%d %H:%M:%f', timestamp, '+0.300 seconds')")
        with pytest.raises(ValueError, match='匹配时间戳不足'):
            fuse_sources(conn, ['A', 'B'])
        rows, meta = fuse_sources(conn, ['A', 'B'], tolerance_seconds=0.5)
    assert len(rows) == 12
    assert meta['offsets']['B']['max_seconds'] == pytest.approx(0.3, abs=1e-5)
    assert rows[7]['source_label_2'] == 1
    assert rows[7]['label'] == 1
    assert rows[7]['source_2'] == 80.0


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf'), 3601, '5', True])
def test_bad_tolerance_is_rejected(tmp_path, bad):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match='时间匹配容差'):
            fuse_sources(conn, ['A', 'B'], tolerance_seconds=bad)


def test_tolerant_training_persists_alignment_and_reuses_on_loading(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE B SET label=0')
        conn.execute("UPDATE B SET timestamp = strftime('%Y-%m-%d %H:%M:%f', timestamp, '+0.2 seconds')")
    contract = {
        'feature_columns': ['value', 'source_2'],
        'resolved_source_tables': ['A', 'B'],
        'source_tables': ['A', 'B'],
        'alignment_tolerance_seconds': 0.5,
        'time_selection': {},
        'model_input_scaling': {'enabled': True, 'method': 'min_max'},
    }
    pipeline = TrainingPipeline(db, limit=0, train_ratio=.75)
    result = pipeline.run('three_sigma', table='A', persist_model=False, data_contract=contract)
    assert result.data_contract['alignment_tolerance_seconds'] == 0.5
    assert result.data_contract['alignment']['alignment_policy'] == 'nearest_unique_with_tolerance'
    assert result.data_contract['alignment']['aligned_count'] == 12
