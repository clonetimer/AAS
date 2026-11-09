"""Cross-partition train/evaluate selection, snapshot export and index integrity."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile

import pytest

from scripts.server import series_catalog as catalog, partition_index
from scripts.server.continuous_series import query_continuous_series, load_logical_training_rows
from scripts.server.logical_selection import normalize_identity, select_role_tables
from scripts.server.services import sqlite_tables
from scripts.server.table_export import build_logical_series_export
from scripts.core.data_loader import DataLoader, extract_binary_labels

IDENT = dict(model='SAT-A', subsystem='ADCS', device='RW1', variable='speed')


def _db(tmp_path):
    db = tmp_path / 'sat.db'
    with sqlite3.connect(db) as conn:
        for name in ('train_a', 'train_b', 'test_a', 'test_b', 'other_channel'):
            conn.execute(f'CREATE TABLE {name}(timestamp TEXT, value REAL, label INTEGER)')
            catalog.register(conn, name, IDENT if name != 'other_channel' else
                             {**IDENT, 'device':'RW2'}, 'compact')
        for idx, name in enumerate(('train_a', 'train_b')):
            conn.executemany(f'INSERT INTO {name} VALUES (?,?,0)',
                [(f'2026-10-01 00:00:{i:02d}', float(i)) for i in range(idx * 8, (idx + 1) * 8)])
        for idx, name in enumerate(('test_a', 'test_b')):
            conn.executemany(f'INSERT INTO {name} VALUES (?,?,?)',
                [(f'2026-10-02 00:00:{i:02d}', float(i), i % 4 == 0)
                 for i in range(idx * 8, (idx + 1) * 8)])
        conn.execute("INSERT INTO other_channel VALUES('2026-10-01 00:00:03',999,0)")
    return db


def test_role_selection_excludes_opposite_role_and_unrelated_channels(tmp_path):
    path = _db(tmp_path)
    items = sqlite_tables(path)
    assert select_role_tables(items, IDENT, 'train') == ['train_a', 'train_b']
    assert select_role_tables(items, IDENT, 'test') == ['test_a', 'test_b']
    assert normalize_identity(IDENT) == IDENT
    with pytest.raises(ValueError, match='缺少'):
        normalize_identity({**IDENT, 'model': ''})


def test_role_scoped_browser_preview_never_mixes_training_and_testing(tmp_path):
    from scripts.server import services
    db = _db(tmp_path)
    training = services.sqlite_continuous_series(db, IDENT, role='train')
    testing = services.sqlite_continuous_series(db, IDENT, role='test')
    assert training['matched_count'] == 16 and testing['matched_count'] == 16
    assert all(s['table'].startswith('train_') for s in training['sources'])
    assert all(s['table'].startswith('test_') for s in testing['sources'])
    with pytest.raises(ValueError, match='用途'):
        services.sqlite_continuous_series(db, IDENT, role='unexpected')


def test_logic_training_split_and_evaluation_single_channel(tmp_path):
    path = _db(tmp_path)
    loader = DataLoader(path, train_ratio=0.75)
    loader.logical_series = IDENT
    loader.logical_role = 'train'
    loader.logical_tables = ['train_a', 'train_b']
    loader.time_column = 'timestamp'
    bundle = loader.load_and_split(table='train_a')
    assert bundle.mode == 'logical_series_ratio_split'
    assert [row['value'] for row in bundle.train_rows] == list(map(float, range(12)))
    assert [row['value'] for row in bundle.test_rows] == list(map(float, range(12, 16)))
    assert bundle.metadata['logical_series_manifest']['matched_count'] == 16
    assert bundle.metadata['logical_series_manifest']['sources'][1]['table'] == 'train_b'
    loader.logical_role = 'test'
    loader.logical_tables = ['test_a', 'test_b']
    loader.train_ratio = 1.0
    result = loader.load_and_split(table='test_a')
    assert result.mode == 'logical_series_simple'
    actual_labels = extract_binary_labels(result.test_value_rows)
    assert len(actual_labels) == 16
    assert actual_labels[0] == 1 and actual_labels[1] == 0
    assert all(row['source_table'].startswith('test_') for row in result.test_rows)


def test_partition_index_is_explicit_and_transactionally_dirty(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        before = query_continuous_series(db, IDENT, limit=0, simulation_db_path=db, connection=conn)
        assert before['matched_count'] == 32
        result = partition_index.refresh(conn, with_timestamp_indexes=True)
        assert result['indexed_tables'] == 5
        assert partition_index.inspect(conn, 'train_a', IDENT)[1]
        conn.execute("INSERT INTO train_a VALUES('2026-10-01 00:00:40',40,0)")
        assert not partition_index.inspect(conn, 'train_a', IDENT)[1]
    after = query_continuous_series(db, IDENT, simulation_db_path=db)
    assert after['matched_count'] == 33
    with sqlite3.connect(db) as conn:
        result = partition_index.refresh(conn)
        assert partition_index.inspect(conn, 'train_a', IDENT)[1]
        conn.execute('DELETE FROM train_b')
        assert not partition_index.inspect(conn, 'train_b', IDENT)[1]
    assert query_continuous_series(db, IDENT, limit=0, simulation_db_path=db)['matched_count'] == 25


def test_export_cross_table_window_and_manifest(tmp_path):
    db = _db(tmp_path)
    file, mime, filename = build_logical_series_export(db, IDENT,
        start_time='2026-10-01 00:00:06', end_time='2026-10-01 00:00:10', simulation_db_path=db)
    try:
        assert mime == 'application/zip' and filename.endswith('.zip')
        with zipfile.ZipFile(file) as archive:
            text = archive.read('telemetry.csv').decode('utf-8-sig')
            rows = list(csv.DictReader(io.StringIO(text)))
            assert [row['value'] for row in rows] == [str(float(i)) for i in range(6, 11)]
            assert [row['source_table'] for row in rows] == ['train_a'] * 2 + ['train_b'] * 3
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['row_count'] == 5
            assert manifest['time_selection']['start'] == '2026-10-01 00:00:06'
            assert len(manifest['source_tables']) == 4
    finally:
        file.close()


def test_manifest_rejects_changed_membership(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError, match='来源表已经变化'):
        load_logical_training_rows(db, IDENT, simulation_db_path=db,
                                   expected_tables=['train_a', 'nonexistent'])


def test_logical_source_conflicts_abort_training_and_export(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO train_b VALUES('2026-10-01 00:00:03',4321,0)")
    with pytest.raises(ValueError, match='冲突'):
        load_logical_training_rows(db, IDENT, simulation_db_path=db,
                                   expected_tables=['train_a', 'train_b'])
    with pytest.raises(ValueError, match='冲突'):
        build_logical_series_export(db, IDENT, simulation_db_path=db)


def test_real_training_pipeline_and_published_detector_evaluation(tmp_path):
    """Run actual algorithm pipeline, not only the logical data loader."""
    from scripts.core.training import TrainingPipeline
    from detectors.statistical_detectors import ZScoreDetector
    from unittest.mock import Mock
    import numpy as np

    db = _db(tmp_path)
    train_contract = {
        'feature_columns': ['value'], 'logical_series': IDENT,
        'logical_tables': ['train_a', 'train_b'], 'logical_role': 'train',
        'time_selection': {'column': 'timestamp', 'start': '2026-10-01 00:00:02',
                           'end': '2026-10-01 00:00:14'},
        'model_input_scaling': {'enabled': True, 'method': 'min_max'},
    }
    result = TrainingPipeline(db, train_ratio=0.75).run('three_sigma', table='train_a',
        persist_model=False, data_contract=train_contract)
    assert result.data_contract['logical_series_manifest']['matched_count'] == 13
    assert result.input_evidence['snapshot_status'] == 'complete'
    assert result.data_contract['time_selection']['start'] == '2026-10-01 00:00:02'

    published_detector = ZScoreDetector({})
    published_detector.fit(np.array([[float(i)] for i in range(16)]))
    published_detector.fit = Mock(side_effect=AssertionError('published model must not re-fit'))
    eval_contract = {
        'feature_columns': ['value'], 'logical_series': IDENT,
        'logical_tables': ['test_a', 'test_b'], 'logical_role': 'test',
        'time_selection': {'column': 'timestamp', 'start': '2026-10-02 00:00:07',
                           'end': '2026-10-02 00:00:12'},
        'model_input_scaling': {'enabled': False},
    }
    evaluated = TrainingPipeline(db, train_ratio=1.0).run('zscore', table='test_a',
        persist_model=False, preloaded_detector=published_detector, data_contract=eval_contract)
    assert evaluated.metrics.mode == 'supervised'
    assert [row['label'] for row in evaluated.result_rows] == [0, 1, 0, 0, 0, 1]
    assert evaluated.data_contract['logical_series_manifest']['matched_count'] == 6
    published_detector.fit.assert_not_called()


def test_logical_test_role_uses_aggregate_label_validity(tmp_path):
    """Two single-class test shards form a valid test set; physical rules stay strict."""
    from scripts.server import services
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE test_a SET label = 0")
        conn.execute("UPDATE test_b SET label = 1")
        services._save_table_role(conn, 'test_a', 'test')
        services._save_table_role(conn, 'test_b', 'test')
    items = sqlite_tables(db)
    assert [item['role_valid'] for item in items if item['name'] in ('test_a', 'test_b')] == [False, False]
    assert select_role_tables(items, IDENT, 'test') == ['test_a', 'test_b']
    result = services.sqlite_continuous_series(db, IDENT, role='test')
    assert result['matched_count'] == 16
    loader = DataLoader(db, train_ratio=1.0)
    loader.logical_series = IDENT
    loader.logical_role = 'test'
    loader.logical_tables = ['test_a', 'test_b']
    bundle = loader.load_and_split(table='test_a')
    assert len(bundle.test_rows) == 16
    assert extract_binary_labels(bundle.test_value_rows) == [0] * 8 + [1] * 8
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE test_b SET label = 0')
    with pytest.raises(ValueError, match='同时包含正常与故障'):
        select_role_tables(sqlite_tables(db), IDENT, 'test')
