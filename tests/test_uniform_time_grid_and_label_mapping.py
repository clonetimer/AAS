"""Cross-rate fusion: bounded uniform timeline, provenance and non-interpolated labels."""
import sqlite3

import pytest

from scripts.core.multi_table_fusion import fuse_sources
from scripts.core.training import TrainingPipeline


def _data(tmp_path):
    path = tmp_path / 'different_rates.db'
    with sqlite3.connect(path) as db:
        for table in ('temperature', 'vibration'):
            db.execute(f'CREATE TABLE "{table}" (timestamp REAL, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)')
        db.executemany('INSERT INTO temperature VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (float(t), 'S', 'ADCS', 'gyro', 'temperature', 20. + t, 0)
            for t in range(0, 21, 5)
        ])
        db.executemany('INSERT INTO vibration VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (float(t), 'S', 'ADCS', 'gyro', 'vibration', t * 0.1, int(t == 8))
            for t in range(0, 21, 2)
        ])
    return path


def test_fixed_grid_uses_real_observations_without_altering_storage(tmp_path):
    db_path = _data(tmp_path)
    with sqlite3.connect(db_path) as db:
        before = db.execute('SELECT * FROM temperature').fetchall()
        rows, report = fuse_sources(db, ['temperature', 'vibration'],
            timeline_mode='fixed', grid_interval_seconds=2,
            alignment_method='previous', tolerance_seconds=5)
        assert db.execute('SELECT * FROM temperature').fetchall() == before
        assert not any(name.startswith('aas_fused_') for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    assert [r['timestamp'] for r in rows] == list(range(0, 21, 2))
    assert rows[1]['value'] == 20.0  # temperature sample held from t=0
    assert rows[1]['source_timestamp_1'] == 0.0
    assert rows[1]['source_timestamp_2'] == 2.0
    assert rows[4]['source_label_2'] == 1
    assert rows[4]['label'] == 1
    assert rows[5]['source_label_2'] == 0
    assert report['grid_count'] == 11
    assert report['grid_coverage'] == 1.0
    assert all(0 <= used <= 1 for used in report['source_usage'].values())
    assert report['timeline_mode'] == 'fixed'
    assert report['label_mapping_policy'] == 'observation_label'


def test_fixed_grid_anchor_independent_and_causal_missing_rejected(tmp_path):
    path = _data(tmp_path)
    with sqlite3.connect(path) as conn:
        rows, meta = fuse_sources(conn, ['temperature','vibration'],
            timeline_mode='fixed', grid_interval_seconds=1,
            tolerance_seconds=5, alignment_method='previous')
        assert len(rows) == 21  # not capped by five temperature observations
        assert meta['source_usage']['temperature'] == 1
        # A 1 second validity window cannot fill long missing runs in temperature.
        with pytest.raises(ValueError, match='匹配时间戳不足'):
            fuse_sources(conn, ['temperature','vibration'],
                timeline_mode='fixed', grid_interval_seconds=.1,
                tolerance_seconds=.01, alignment_method='previous')


def test_mapped_and_previous_labels_differ_and_never_interpolate(tmp_path):
    path = _data(tmp_path)
    with sqlite3.connect(path) as conn:
        # Dense sources with offset phases: matched-nearest may see a later
        # faulty measurement, but a prior-known label remains normal.
        conn.execute('DELETE FROM temperature')
        conn.executemany('INSERT INTO temperature VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (float(t), 'S','ADCS','gyro','temperature', 20+t, int(t == 1))
            for t in range(21)
        ])
        conn.execute('UPDATE vibration SET timestamp=timestamp+0.6')
        matched, _ = fuse_sources(conn, ['temperature','vibration'],
            timeline_mode='fixed', grid_interval_seconds=1,
            tolerance_seconds=2, alignment_method='nearest',
            label_alignment_method='matched')
        historical, meta = fuse_sources(conn, ['temperature','vibration'],
            timeline_mode='fixed', grid_interval_seconds=1,
            tolerance_seconds=2, alignment_method='nearest',
            label_alignment_method='previous')
    # Grid t=0.6: latest *previous* temperature sample t=0 normal,
    # but the nearest measurement at t=1 has a fault label.
    assert next(r for r in matched if r['timestamp'] == pytest.approx(0.6))['source_label_1'] == 1
    assert next(r for r in historical if r['timestamp'] == pytest.approx(0.6))['source_label_1'] == 0
    assert meta['label_mapping_policy'] == 'causal_previous_label'


@pytest.mark.parametrize('kwargs,expected',[ 
    ({'timeline_mode':'invalid'}, '时间轴'),
    ({'timeline_mode':'fixed','grid_interval_seconds':0,'tolerance_seconds':1}, '采样间隔'),
    ({'timeline_mode':'fixed','grid_interval_seconds':1,'tolerance_seconds':0}, '容差'),
    ({'timeline_mode':'anchor','grid_interval_seconds':1}, '主表时间轴'),
    ({'label_alignment_method':'spline'}, '标签映射'),
    ({'timeline_mode':'fixed','grid_interval_seconds':0.001,'tolerance_seconds':1}, '超过'),
])
def test_invalid_grid_contract_rejected(tmp_path, kwargs, expected):
    path = _data(tmp_path)
    with sqlite3.connect(path) as conn:
        if expected == '超过':
            conn.execute('UPDATE temperature SET timestamp=500 WHERE timestamp=20')
            conn.execute('UPDATE vibration SET timestamp=500 WHERE timestamp=20')
        with pytest.raises(ValueError, match=expected):
            fuse_sources(conn,['temperature','vibration'], **kwargs)


def test_saved_training_contract_carries_uniform_grid_and_label_rule(tmp_path):
    path = _data(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE vibration SET label=0')
    contract = {
        'feature_columns':['value','source_2'],
        'source_tables':['temperature','vibration'],
        'resolved_source_tables':['temperature','vibration'],
        'timeline_mode':'fixed', 'grid_interval_seconds':2,
        'alignment_method':'previous', 'alignment_tolerance_seconds':5,
        'label_alignment_method':'previous',
        'time_selection':{},
        'model_input_scaling':{'enabled':True,'method':'min_max'},
    }
    result = TrainingPipeline(path, limit=0, train_ratio=0.75).run(
        'three_sigma', table='temperature',persist_model=False,data_contract=contract)
    assert result.data_contract['timeline_mode'] == 'fixed'
    assert result.data_contract['alignment']['grid_count'] == 11
    assert result.data_contract['alignment']['label_mapping_policy'] == 'causal_previous_label'
    assert result.data_contract['alignment']['grid_interval_seconds'] == 2
