"""Physical time partitions behave as one read-only logical measurement."""
import sqlite3
from pathlib import Path

import pytest

from scripts.server import series_catalog as catalog
from scripts.server import services

IDENTITY = dict(model='SAT-A', subsystem='ADCS', device='RW1', variable='speed')


def _build_db(path: Path):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE first_chunk(timestamp TEXT, value REAL, label INTEGER)')
        conn.execute('CREATE TABLE second_chunk(timestamp TEXT, value REAL, label INTEGER)')
        conn.execute('CREATE TABLE other_rw(timestamp TEXT, value REAL, label INTEGER)')
        conn.executemany('INSERT INTO first_chunk VALUES (?,?,?)', [
            (f'2026-10-01 00:00:{i:02d}', float(i), 0) for i in range(5)])
        conn.executemany('INSERT INTO second_chunk VALUES (?,?,?)', [
            (f'2026-10-01 00:00:{i:02d}', float(i), int(i == 7)) for i in range(5, 10)])
        conn.execute('INSERT INTO other_rw VALUES (?, ?, ?)', ('2026-10-01 00:00:06', 999, 1))
        for table in ('first_chunk', 'second_chunk'):
            catalog.register(conn, table, IDENTITY, 'compact')
        catalog.register(conn, 'other_rw', {**IDENTITY, 'device':'RW2'}, 'compact')


def _query(path, **kwargs):
    return services.sqlite_continuous_series(path, IDENTITY, **kwargs)


def test_cross_physical_tables_sorted_and_provenance(tmp_path):
    db = tmp_path / 'series.db'
    _build_db(db)
    data = _query(db)
    assert [row['value'] for row in data['rows']] == list(range(10))
    assert [row['source_table'] for row in data['rows']] == ['first_chunk'] * 5 + ['second_chunk'] * 5
    assert all(row['device'] == 'RW1' for row in data['rows'])
    assert data['matched_count'] == 10 and not data['truncated']
    assert data['min'] == '2026-10-01 00:00:00' and data['max'] == '2026-10-01 00:00:09'
    assert len(data['sources']) == 2
    assert data['rows'][7]['label'] == 1


def test_time_range_crosses_partition_boundary_and_pages_globally(tmp_path):
    db = tmp_path / 'series.db'
    _build_db(db)
    data = _query(db, start_time='2026-10-01 00:00:03', end_time='2026-10-01 00:00:07', limit=3)
    assert data['matched_count'] == 5 and data['truncated']
    assert [row['value'] for row in data['rows']] == [3., 4., 5.]
    assert [s['matched_count'] for s in data['sources']] == [2, 3]
    page = _query(db, start_time='2026-10-01 00:00:03', end_time='2026-10-01 00:00:07', limit=4, offset=2)
    assert [row['value'] for row in page['rows']] == [5., 6., 7.]
    empty = _query(db, start_time='2026-12-01', end_time='2026-12-02')
    assert empty['rows'] == [] and empty['matched_count'] == 0
    assert len(empty['sources']) == 2


def test_duplicate_equal_rows_are_merged_but_conflicting_labels_block(tmp_path):
    db = tmp_path / 'series.db'
    _build_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO second_chunk VALUES (?,?,?)', ('2026-10-01 00:00:04', 4.0, 0))
    data = _query(db)
    assert data['duplicate_count'] == 1
    assert len(data['rows']) == 10
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE second_chunk SET label=1 WHERE timestamp=?', ('2026-10-01 00:00:04',))
    with pytest.raises(ValueError, match='标签冲突|数据或标签冲突'):
        _query(db)


def test_bad_identity_and_invalid_timestamp_or_bad_pagination_fail(tmp_path):
    db = tmp_path / 'series.db'
    _build_db(db)
    with pytest.raises(ValueError, match='必须完整'):
        services.sqlite_continuous_series(db, {**IDENTITY, 'device': ''})
    with pytest.raises(ValueError, match='limit'):
        _query(db, limit=100000)
    with pytest.raises(ValueError, match='起始时间'):
        _query(db, start_time='2026-10-03', end_time='2026-10-01')
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE second_chunk SET timestamp=REPLACE(timestamp, " ", "T")')
    with pytest.raises(ValueError, match='格式不一致'):
        _query(db)


def test_mismatched_legacy_wide_table_excluded(tmp_path):
    db = tmp_path / 'series.db'
    _build_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE legacy_wide (timestamp TEXT,model TEXT,subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER,extra_sensor REAL)')
        conn.execute('INSERT INTO legacy_wide VALUES (?,?,?,?,?,?,?,?)',
                     ('2026-10-01 00:00:11', *IDENTITY.values(), 123, 0, 10))
    assert _query(db)['matched_count'] == 10


def test_ui_exposes_first_class_channel_picker_and_server_query():
    root = Path(__file__).resolve().parents[1] / 'web' / 'visualization'
    html = (root / 'browser.html').read_text(encoding='utf-8')
    js = (root / 'browser.js').read_text(encoding='utf-8')
    for name in ('seriesModel', 'seriesSubsystem', 'seriesDevice', 'seriesVariable',
                 'browseContinuousSeriesBtn', 'browseTablesModeBtn', 'logicalSeriesStatus'):
        assert f'id="{name}"' in html
    assert '/api/telemetry-series' in js
    assert 'async function enterContinuousSeries()' in js
    assert 'state.logicalSeries ? null : currentDataTableName()' in js
    assert 'gapIndices' in js
