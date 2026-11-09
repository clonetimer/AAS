from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts.server import data_routes
from scripts.server.telemetry_v2_access import query_charts
from tests.test_p3e_chart_overview import chart_db


def _page(db, *, cursor=None, role='train', start=None, end=None, channel_id=1, limit=100):
    return query_charts(
        db, role=role, channel_ids=[channel_id], start=start, end=end,
        limit=limit, mode='page', cursor=cursor)['series'][0]


def test_sample_pages_are_stable_and_have_no_duplicate_or_missing_frames(tmp_path):
    db = chart_db(tmp_path, frames=500)
    pages = []
    cursor = None
    while True:
        page = _page(db, cursor=cursor)
        pages.append(page)
        if not page['page_has_more']:
            assert page['next_cursor'] is None
            break
        cursor = page['next_cursor']
        assert isinstance(cursor, str) and cursor

    points = [point for page in pages for point in page['points']]
    keys = [point['time_key'] for point in points]
    assert len(pages) == 5
    assert len(points) == 500
    assert keys == [float(index) for index in range(500)]
    assert [point['frame_index'] for point in points] == list(range(1, 501))
    assert sum(page['page_row_count'] for page in pages) == 500
    assert all(page['frame_count'] == 500 for page in pages)
    assert all(page['valid_sample_count'] == 490 for page in pages)
    assert sum(point['value'] is None for point in points) == 10


def test_sample_cursor_is_bound_to_channel_role_and_time_window(tmp_path):
    db = chart_db(tmp_path, frames=40)
    first = _page(db, start=0, end=20, limit=5)
    cursor = first['next_cursor']
    assert cursor

    with pytest.raises(ValueError, match='游标'):
        _page(db, cursor=cursor, start=1, end=20, limit=5)
    with pytest.raises(ValueError, match='游标'):
        _page(db, cursor=cursor, start=0, end=20, role='test', limit=5)


def test_malformed_sample_cursor_is_rejected(tmp_path):
    db = chart_db(tmp_path, frames=40)
    with pytest.raises(ValueError, match='游标'):
        _page(db, cursor='not-a-valid-cursor', limit=5)


def test_page_mode_returns_exact_logical_frames_and_collapses_identical_duplicates(tmp_path):
    db = chart_db(tmp_path, frames=40)
    first = _page(db, limit=20)
    assert first['frame_count'] == 40
    assert first['valid_sample_count'] == 39
    assert first['duplicate_count'] == 1
    assert first['plot_point_count'] == 0
    assert first['page_row_count'] == 20
    assert first['points'][0]['frame_index'] == 1
    assert first['points'][10]['time_key'] == 10.0
    assert first['points'][10]['value'] == 10.0
    assert any(point['value'] is None for point in first['points'])


def test_conflicting_duplicate_anywhere_in_window_rejects_page_query(tmp_path):
    db = chart_db(tmp_path, frames=500)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (2,1,1,'499','str','numeric_unspecified',499,12345,'12345',0,'0','{}')''')
    with pytest.raises(ValueError, match='冲突采样'):
        _page(db, limit=5)


def test_http_page_query_uses_page_default_and_returns_cursor(tmp_path, monkeypatch):
    db = chart_db(tmp_path, frames=40)
    monkeypatch.setattr(data_routes, '_browser_db', lambda _main, _name: db)

    class Main:
        @staticmethod
        def json_response(_handler, payload, status=200):
            return status, payload

    status, payload = data_routes.POST_DATA_ROUTES['/api/v2/telemetry/query'](
        None, SimpleNamespace(path='/api/v2/telemetry/query'), Main,
        json.dumps({'role': 'train', 'channel_ids': [1], 'mode': 'page'}).encode('utf-8'))

    assert status == 200
    series = payload['series'][0]
    assert series['page_row_count'] == 40
    assert series['page_has_more'] is False
    assert series['next_cursor'] is None


def test_cursor_rejects_database_shape_change_between_pages(tmp_path):
    db = chart_db(tmp_path, frames=40)
    first = _page(db, limit=5)
    cursor = first['next_cursor']
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (1,1,100,'40','str','numeric_unspecified',40,40,'40',0,'0','{}')''')
    with pytest.raises(ValueError, match='数据在分页期间发生变化'):
        _page(db, cursor=cursor, limit=5)



@pytest.mark.parametrize('domain', ['datetime_utc', 'datetime_naive'])
def test_page_cursor_preserves_datetime_microsecond_precision(tmp_path, domain):
    import sqlite3
    from scripts.rebuild_telemetry_v2 import create_v2

    db = tmp_path / f'{domain}.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
        conn.execute(
            '''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
               imported_utc,source_table_count,source_row_count) VALUES (1,'mission','time.csv','sha',100,'2026-11-07',1,3)''')
        conn.execute(
            '''INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit)
               VALUES (1,'mission','SAT','ADCS','RW','speed','rpm')''')
        conn.execute(
            '''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
               role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,
               time_start_raw,time_end_raw) VALUES (1,1,'time.csv',1,'telemetry','train',
               'aas_table_meta',?,1,'[]',3,?,?)''',
            (domain,
             '2026-11-07T00:00:00.000001Z' if domain == 'datetime_utc' else '2026-11-07T00:00:00.000001',
             '2026-11-07T00:00:00.000003Z' if domain == 'datetime_utc' else '2026-11-07T00:00:00.000003'))
        rows = []
        for index in range(1, 4):
            raw = f'2026-11-07T00:00:00.{index:06d}' + ('Z' if domain == 'datetime_utc' else '')
            utc_us = 1794009600000000 + index if domain == 'datetime_utc' else None
            sort_text = raw.replace('Z', '') if domain == 'datetime_naive' else None
            rows.append((1, 1, index, raw, 'str', domain, utc_us, sort_text,
                         float(index), str(index), 0, '0', '{}'))
        conn.executemany(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_utc_us,time_sort_text,value_num,value_raw_json,
               label_int,label_raw_json,raw_row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
        conn.commit()

    first = query_charts(db, role='train', channel_ids=[1], mode='page', limit=1)['series'][0]
    second = query_charts(db, role='train', channel_ids=[1], mode='page', limit=1,
                          cursor=first['next_cursor'])['series'][0]
    assert first['points'][0]['time_key'] != second['points'][0]['time_key']
    assert first['points'][0]['frame_index'] == 1
    assert second['points'][0]['frame_index'] == 2


def test_page_mode_defaults_to_one_hundred_rows(tmp_path):
    db = chart_db(tmp_path, frames=120)
    page = query_charts(db, role='train', channel_ids=[1], mode='page')['series'][0]
    assert page['page_row_count'] == 100
    assert page['page_has_more'] is True


def test_cursor_detects_same_count_change_before_last_page_key(tmp_path):
    db = chart_db(tmp_path, frames=40)
    first = _page(db, limit=5)
    cursor = first['next_cursor']
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE v2_samples SET time_sort_num=10.5,timestamp_raw='10.5' WHERE segment_id=1 AND time_sort_num=2")
    with pytest.raises(ValueError, match='数据在分页期间发生变化'):
        _page(db, cursor=cursor, limit=5)


def test_cursor_rejects_value_mutation_even_when_time_keys_and_counts_are_unchanged(tmp_path):
    """A cursor must be invalidated if sample values change between pages."""
    import sqlite3

    db = chart_db(tmp_path, frames=120)
    first = _page(db, limit=10)
    cursor = first['next_cursor']
    with sqlite3.connect(db) as conn:
        conn.execute(
            'UPDATE v2_samples SET value_num=987654 WHERE segment_id=1 AND time_sort_num=80')

    with pytest.raises(ValueError, match='数据在分页期间发生变化'):
        _page(db, cursor=cursor, limit=10)


def test_page_mode_rejects_sample_time_domain_mismatch_instead_of_hiding_rows(tmp_path):
    import sqlite3

    db = chart_db(tmp_path, frames=20)
    with sqlite3.connect(db) as conn:
        conn.execute(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (1,1,100,'20','str','datetime_utc',20,20,'20',0,'0','{}')''')

    with pytest.raises(ValueError, match='时间域与来源分片不一致'):
        _page(db, limit=5)
