"""Direct CSV preprocessing and persisted V2 derived-series export."""
from __future__ import annotations

import csv
import gzip
import io
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_preprocess import preprocess, list_quality_jobs, export_job_csv
from scripts.server.v2_csv_preprocess import inspect, process


def rows(payload):
    return list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))


def test_local_frame_csv_preserves_labels_missing_and_source_values():
    original = b'frame,gyro_x,gyro_y,label\n1,1,3,0\n2,NA,4,0\n3,2,5,1\n4,3,6,1\n'
    info = inspect(original, 'sensor.csv')
    assert info['coordinate_mode'] == 'frame'
    assert info['numeric_columns'] == ['gyro_x', 'gyro_y']
    content, summary = process(original, 'sensor.csv', {
        'column': 'gyro_x', 'method': 'hampel_forward', 'params': {'window': 3},
        'role': 'train', 'sha256': info['sha256']})
    exported = rows(content)
    assert len(exported) == 4
    assert [item['label'] for item in exported] == ['0', '0', '1', '1']
    assert exported[1]['gyro_x'] == 'NA'
    assert exported[1]['gyro_x__hampel_forward'] == '1'
    assert exported[1]['aas_quality'] == 'filled_missing'
    assert summary['source_missing'] == 1


def test_local_csv_rejects_frame_gaps_and_test_fit():
    bad = b'frame,value\n1,1\n3,2\n'
    with pytest.raises(ValueError, match='连续递增'):
        inspect(bad, 'bad.csv')
    good = b'frame,value,label\n1,1,0\n2,2,1\n3,3,1\n'
    info = inspect(good, 'good.csv')
    with pytest.raises(ValueError, match='不能重新拟合'):
        process(good, 'good.csv', {'column': 'value', 'method': 'zscore',
            'role': 'test', 'params': {}, 'sha256': info['sha256']})
    with pytest.raises(ValueError, match='重新检查'):
        process(good, 'good.csv', {'column': 'value', 'method': 'clip',
            'role': 'train', 'params': {'min': 0, 'max': 2}, 'sha256': '0' * 64})


def test_local_time_csv_keeps_gap_boundary_and_timestamp():
    original = b'time_s,value,label\n0,1,0\n1,,0\n100,,1\n101,2,1\n'
    info = inspect(original, 'clock.csv')
    assert info['coordinate_mode'] == 'time'
    content, _ = process(original, 'clock.csv', {'column': 'value',
        'method': 'hampel_forward', 'role': 'train', 'params': {'window': 3},
        'sha256': info['sha256']})
    exported = rows(content)
    assert [item['time_s'] for item in exported] == ['0', '1', '100', '101']
    assert exported[2]['value__hampel_forward'] == ''
    assert exported[2]['aas_quality'] == 'unfilled_missing'


def test_gzip_gb18030_csv_is_processed_without_database(tmp_path):
    raw = 'frame,转速,label\n1,3,0\n2,4,1\n3,5,1\n'.encode('gb18030')
    compressed = gzip.compress(raw)
    info = inspect(compressed, '转速.csv.gz')
    assert info['csv_encoding'] == 'gb18030'
    content, _ = process(compressed, '转速.csv.gz', {'column': '转速',
        'method': 'moving_average', 'role': 'train', 'params': {'window': 2},
        'sha256': info['sha256'], 'encoding': info['csv_encoding'],
        'delimiter': info['csv_delimiter']})
    assert rows(content)[1]['转速__moving_average'] == '3.5'
    assert not (tmp_path / 'datasets').exists()


def test_persisted_derived_series_exports_quality_without_mutating_source(tmp_path):
    db = tmp_path / 'telemetry.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
    imported = import_csv(db, dict(namespace='test', model='M1', subsystem='ADCS',
        device='RW', variable='speed', unit='rpm', role='train',
        time_domain='numeric_unspecified', time_reference='clock', time_unit='s',
        filename='sample.csv', csv='timestamp,value,label\n0,1,0\n1,,0\n2,2,0\n'), commit=True)
    preprocess(db, {'channel_id': imported['channel_id'], 'role': 'train',
        'method': 'hampel_forward', 'params': {'window': 3}, 'confirm': True}, commit=True)
    job = list_quality_jobs(db)['items'][0]
    exported = rows(export_job_csv(db, job['id']))
    assert len(exported) == 3
    assert exported[1]['processed_value'] == '1.0'
    assert exported[1]['quality_status'] == 'filled_missing'
    assert exported[1]['timestamp_raw'] == '1'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0] == 6
