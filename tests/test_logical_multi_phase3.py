"""Phase 3: two sharded telemetry channels become two aligned features."""
import sqlite3
from copy import deepcopy

import pytest

from scripts.server import series_catalog as catalog
from scripts.core.logical_multi_fusion import resolve_logical_sources, fuse_logical_sources
from scripts.core.data_loader import DataLoader, extract_binary_labels

SPEED = dict(model='SAT-A', subsystem='ADCS', device='RW1', variable='speed')
CURRENT = {**SPEED, 'variable': 'current'}


def _db(tmp_path):
    path = tmp_path / 'test.db'
    with sqlite3.connect(path) as conn:
        for ident, prefix in ((SPEED,'speed'), (CURRENT,'current')):
            for purpose in ('train','test'):
                for shard in range(2):
                    name = f'{prefix}_{purpose}_{shard}'
                    conn.execute(f'CREATE TABLE {name}(timestamp TEXT,value REAL,label INTEGER)')
                    catalog.register(conn, name, ident, 'compact')
                    for t in range(shard * 8, (shard + 1) * 8):
                        label = int(purpose == 'test' and t % 5 == 0)
                        conn.execute(f'INSERT INTO {name} VALUES(?,?,?)',
                            (f'2026-01-01 00:00:{t:02d}', float(t + (100 if prefix == 'current' else 0)), label))
    return path


def _train_sources(path):
    return resolve_logical_sources(path, [SPEED, CURRENT], role='train')


def test_concat_before_alignment_maintains_two_dimensions(tmp_path):
    db = _db(tmp_path)
    sources = _train_sources(db)
    assert sources == [
        {'identity': SPEED, 'tables':['speed_train_0','speed_train_1']},
        {'identity': CURRENT, 'tables':['current_train_0','current_train_1']},
    ]
    rows, info = fuse_logical_sources(db, sources, role='train', simulation_db_path=db)
    assert len(rows) == 16
    assert info['feature_columns'] == ['value','source_2']
    assert [r['value'] for r in rows] == list(map(float,range(16)))
    assert [r['source_2'] for r in rows] == list(map(float,range(100,116)))
    assert info['fusion_kind'] == 'concatenate_each_channel_then_align'
    assert [item['tables'] for item in info['logical_sources']] == [
        ['speed_train_0', 'speed_train_1'], ['current_train_0', 'current_train_1']]
    assert info['source_mapping']['source_2'] == CURRENT


def test_loader_time_window_and_chronological_split(tmp_path):
    db = _db(tmp_path)
    loader = DataLoader(db, train_ratio=.75, time_column='timestamp',
                        time_start='2026-01-01 00:00:06', time_end='2026-01-01 00:00:13')
    loader.logical_multi_sources = _train_sources(db)
    loader.logical_role = 'train'
    bundle = loader.load_and_split(table='speed_train_0')
    assert bundle.mode == 'logical_multi_ratio_split'
    assert bundle.train_count == 6 and bundle.test_count == 2
    assert bundle.train_values[0] == [6., 106.]
    assert bundle.test_values[-1] == [13., 113.]
    assert bundle.metadata['alignment']['aligned_count'] == 8
    assert extract_binary_labels(bundle.test_value_rows) == [0,0]


def test_logical_multichannel_test_labels_and_shard_separation(tmp_path):
    db = _db(tmp_path)
    sources = resolve_logical_sources(db, [SPEED,CURRENT], role='test')
    rows, info = fuse_logical_sources(db, sources, role='test', simulation_db_path=db)
    assert len(rows) == 16 and {row['label'] for row in rows} == {0,1}
    assert all('train_' not in item['table'] for source in info['logical_sources']
               for item in source['physical_sources'])
    loader = DataLoader(db, train_ratio=1.)
    loader.logical_multi_sources = sources
    loader.logical_role = 'test'
    bundle = loader.load_and_split(table='speed_test_0')
    assert bundle.mode == 'logical_multi_simple'
    assert len(extract_binary_labels(bundle.test_value_rows)) == 16


def test_membership_and_conflict_changes_fail_closed(tmp_path):
    db = _db(tmp_path)
    sources = _train_sources(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO current_train_1 VALUES('2026-01-01 00:00:04',999,0)")
    with pytest.raises(ValueError,match='冲突'):
        fuse_logical_sources(db, sources, role='train', simulation_db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM current_train_1 WHERE timestamp='2026-01-01 00:00:04'")
        conn.execute('CREATE TABLE current_train_2(timestamp TEXT,value REAL,label INTEGER)')
        catalog.register(conn, 'current_train_2', CURRENT, 'compact')
        conn.execute("INSERT INTO current_train_2 VALUES('2026-01-01 00:00:40',123,0)")
    with pytest.raises(ValueError,match='变化'):
        fuse_logical_sources(db, sources, role='train', simulation_db_path=db)


def test_duplicate_logical_identity_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError,match='重复'):
        resolve_logical_sources(db, [SPEED,deepcopy(SPEED)],role='train')


def test_causal_asof_stays_in_past_across_shards(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM current_train_0 WHERE timestamp LIKE '%:03' OR timestamp LIKE '%:07'")
    sources = _train_sources(db)
    rows, info = fuse_logical_sources(db, sources, role='train', simulation_db_path=db,
                                     tolerance_seconds=2, alignment_method='previous')
    row3 = next(item for item in rows if item['value'] == 3.)
    assert row3['source_2'] == 102.
    assert row3['source_timestamp_2'] < row3['timestamp']
    assert info['alignment_policy'] == 'previous_with_tolerance'


def test_pipeline_records_source_contract(tmp_path):
    from scripts.core.training import TrainingPipeline
    db = _db(tmp_path)
    sources = _train_sources(db)
    contract = {
        'version':5, 'feature_columns':['value','source_2'],
        'logical_multi_sources':sources, 'logical_role':'train',
        'model_input_scaling':{'enabled':True,'method':'min_max'},
        'time_selection':{'column':'timestamp','start':None,'end':None},
    }
    result = TrainingPipeline(db,train_ratio=.75).run('three_sigma',table='speed_train_0',
        persist_model=False,data_contract=contract)
    assert result.data_contract['alignment']['fusion_kind'] == 'concatenate_each_channel_then_align'
    assert len(result.data_contract['alignment']['logical_sources']) == 2
    assert result.input_evidence['snapshot_status'] == 'complete'


def test_provenance_includes_all_physical_fragments(tmp_path):
    from scripts.server.execution_provenance import source_tables
    db = _db(tmp_path)
    channels = _train_sources(db)
    names = source_tables({'table':'speed_train_0','sourceTables':['speed_train_0','current_train_0'],
                           'logicalMultiSources':channels})
    assert set(names) == {'speed_train_0','speed_train_1','current_train_0','current_train_1'}


def test_window_scope_skips_out_of_window_max_row_limit(tmp_path,monkeypatch):
    from scripts.core import multi_table_fusion
    db = _db(tmp_path)
    sources = _train_sources(db)
    monkeypatch.setattr(multi_table_fusion,'MAX_ROWS_PER_SOURCE',6)
    from scripts.core import logical_multi_fusion
    monkeypatch.setattr(logical_multi_fusion,'MAX_ROWS_PER_SOURCE',6)
    rows,_ = fuse_logical_sources(db,sources,role='train',simulation_db_path=db,
        time_start='2026-01-01 00:00:09', time_end='2026-01-01 00:00:13')
    assert [row['value'] for row in rows]==[9.,10.,11.,12.,13.]


def test_evaluate_published_multifeature_detector_without_refitting(tmp_path):
    import numpy as np
    from unittest.mock import Mock
    from detectors.statistical_detectors import ZScoreDetector
    from scripts.core.training import TrainingPipeline
    db = _db(tmp_path)
    eval_sources = resolve_logical_sources(db,[SPEED,CURRENT],role='test')
    published = ZScoreDetector({})
    published.fit(np.array([[float(n),float(100+n)] for n in range(16)]))
    published.fit = Mock(side_effect=AssertionError('evaluation must not refit'))
    contract = {
        'version':5, 'feature_columns':['value','source_2'],
        'logical_multi_sources':eval_sources,'logical_role':'test',
        'model_input_scaling':{'enabled':False},
        'time_selection':{'column':'timestamp','start':None,'end':None},
    }
    result = TrainingPipeline(db,train_ratio=1.0).run('zscore',table='speed_test_0',
        persist_model=False, preloaded_detector=published, data_contract=contract)
    assert result.metrics.mode == 'supervised'
    assert result.data_contract['alignment']['aligned_count'] == 16
    assert {row['label'] for row in result.result_rows} == {0,1}
    published.fit.assert_not_called()


def test_test_channel_may_have_single_class_if_union_has_both(tmp_path):
    from scripts.server import services
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE current_test_0 SET label=0')
        conn.execute('UPDATE current_test_1 SET label=0')
        services._save_table_role(conn,'current_test_0','test')
        services._save_table_role(conn,'current_test_1','test')
    sources = resolve_logical_sources(db,[SPEED,CURRENT],role='test')
    rows,meta = fuse_logical_sources(db,sources,role='test',simulation_db_path=db)
    assert len(rows) == 16 and {row['label'] for row in rows} == {0,1}
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE speed_test_0 SET label=0')
        conn.execute('UPDATE speed_test_1 SET label=0')
        services._save_table_role(conn,'speed_test_0','test')
        services._save_table_role(conn,'speed_test_1','test')
    sources = resolve_logical_sources(db,[SPEED,CURRENT],role='test')
    with pytest.raises(ValueError,match='同时包含正常与故障'):
        fuse_logical_sources(db,sources,role='test',simulation_db_path=db)
