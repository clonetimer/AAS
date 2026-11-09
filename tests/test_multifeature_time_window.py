"""Multi-feature selection, chronological windowing and mapped import regression."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

from scripts.core.data_loader import DataLoader
from scripts.core.training import TrainingPipeline
from scripts.server.services import _apply_field_mappings


def _create_db(path):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE train(timestamp TEXT, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, f1 REAL, f2 REAL, label INTEGER)')
        # Reverse insertion order so chronological sort is required.
        connection.executemany('INSERT INTO train VALUES (?,?,?,?,?,?,?,?,?)', [
            (f'2026-01-01 00:{i:02d}:00','DEMO','ADCS','unit','multisensor',
             i/10, i*2.0, i*3.0, 0) for i in range(49,-1,-1)
        ])


def test_window_before_limit_and_chronological_split(tmp_path):
    db = tmp_path / 'test.db'
    _create_db(db)
    dl = DataLoader(db, limit=9, train_ratio=.6,
                    feature_columns=['f1','f2'], time_column='timestamp',
                    time_start='2026-01-01 00:10:00', time_end='2026-01-01 00:29:00')
    bundle = dl.load_and_split(table='train')
    assert bundle.train_count == 5 and bundle.test_count == 4
    assert bundle.train_rows[0]['timestamp'] == '2026-01-01 00:10:00'
    assert bundle.train_values[0] == [20.0,30.0]
    assert bundle.test_rows[-1]['timestamp'] == '2026-01-01 00:18:00'
    assert bundle.metadata['feature_columns'] == ['f1','f2']


def test_two_feature_pipeline_and_scaler_persists_per_dimension(tmp_path):
    db = tmp_path / 'train.db'
    _create_db(db)
    pipeline = TrainingPipeline(db, limit=0, train_ratio=.7)
    result = pipeline.run('three_sigma', table='train', persist_model=False,
                          data_contract={'feature_columns':['f1','f2'],
                                         'time_selection': {'column':'timestamp',
                                                            'start':'2026-01-01 00:10:00',
                                                            'end':'2026-01-01 00:45:00'},
                                         'model_input_scaling': {'enabled':True, 'method':'min_max'}})
    assert result.data_contract['feature_columns'] == ['f1','f2']
    scaler = result.data_contract['model_input_scaling']['parameters']['per_feature']
    assert list(scaler) == ['f1', 'f2']
    assert scaler['f1']['max'] == 68.0
    assert scaler['f2']['max'] == 102.0
    assert result.result_rows[0]['value'] == 70.0
    assert len(result.result_rows) == len(result.threshold)


def test_missing_feature_and_empty_window_rejected(tmp_path):
    db = tmp_path / 'bad.db'
    _create_db(db)
    with pytest.raises(ValueError, match='缺少所选特征'):
        DataLoader(db, feature_columns=['f1','nonexistent']).load_and_split(table='train')
    with pytest.raises(ValueError, match='训练集'):
        DataLoader(db, feature_columns=['f1'], time_column='timestamp',
                   time_start='2030-01-01', time_end='2030-01-02').load_and_split(table='train')


def test_csv_mapping_preserves_other_numeric_features():
    source = [
        {'when':'2026-01-01', 'sensor1':'10','sensor2':'20','sensor3':'30','label':'0'},
        {'when':'2026-01-02', 'sensor1':'11','sensor2':'21','sensor3':'31','label':'0'},
    ]
    maps = {'timestamp':{'type':'column','value':'when'},
            'value':{'type':'column','value':'sensor1'},
            'label':{'type':'column','value':'label'},
            'model':{'type':'manual','value':'DEMO'},
            'subsystem':{'type':'manual','value':'ADCS'},
            'device':{'type':'manual','value':'unit'},
            'variable':{'type':'manual','value':'multisensor'}}
    result = _apply_field_mappings(source, maps)
    assert result[0]['value'] == '10'
    assert result[0]['sensor2'] == '20'
    assert result[0]['sensor3'] == '30'
    assert 'sensor1' not in result[0]


def test_ui_contract():
    root = Path(__file__).resolve().parents[1] / 'web' / 'visualization'
    browser = (root/'browser.html').read_text(encoding='utf-8')
    runner = (root/'runner.html').read_text(encoding='utf-8')
    assert browser.index('id="importBtn"') < browser.index('id="deleteTableBtn"') < browser.index('id="statsGrid"')
    assert 'id="manageTablesModal"' in browser
    assert browser.count('id="deleteTableBtn"') == 1
    for name in ['additionalTrainTables','trainTimeColumn','trainTimeStart','trainTimeEnd']:
        assert f'id="{name}"' in runner
