from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.server import telemetry_v2_access as access
from scripts.server.data_routes import GET_DATA_ROUTES


@pytest.fixture()
def catalog_db(tmp_path: Path) -> Path:
    db = tmp_path / 'catalog.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript(CANON_SCHEMA)
        conn.execute("INSERT INTO v2_meta VALUES ('schema_version','2')")
        for source_id, namespace in ((1, 'mission-A'), (2, 'mission-B')):
            conn.execute(
                '''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
                   imported_utc,source_table_count,source_row_count) VALUES (?,?,?,?,?,?,?,?)''',
                (source_id, namespace, f'source-{source_id}.csv', f'hash-{source_id}', 100,
                 '2026-11-07', 1, 12))
        for cid in range(1, 7):
            namespace = 'mission-A' if cid <= 4 else 'mission-B'
            source_id = 1 if cid <= 4 else 2
            conn.execute(
                '''INSERT INTO v2_channels(id,namespace,satellite_instance,model,subsystem,device,variable,unit)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (cid, namespace, f'sat-{cid}', 'SAT-A', 'ADCS', f'RW-{cid}',
                 'speed', 'rpm'))
            role = 'test' if cid == 6 else 'train'
            conn.execute(
                '''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
                   role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,
                   time_start_raw,time_end_raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (cid, source_id, f'part-{cid}', cid, 'telemetry', role, 'aas_table_meta',
                 'datetime_naive', 1, '[]', 3,
                 '2026-11-07 00:00:00', '2026-11-07 00:00:02'))
            conn.executemany(
                '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
                   timestamp_type,time_domain,time_sort_text,value_num,value_raw_json,label_int,
                   label_raw_json,raw_row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                [(cid, cid, i, f'2026-11-07 00:00:0{i}', 'str', 'datetime_naive',
                  f'2026-11-07T00:00:0{i}.000000', float(cid * 10 + i),
                  str(cid * 10 + i), 0, '0', '{}') for i in range(3)])
        conn.commit()
    return db


def test_list_channels_returns_stable_sql_pages_and_total_count(catalog_db):
    first = access.list_channels(catalog_db, role='train', limit=2, offset=0)
    second = access.list_channels(catalog_db, role='train', limit=2, offset=2)
    last = access.list_channels(catalog_db, role='train', limit=2, offset=4)

    assert first['count'] == 5
    assert first['limit'] == 2 and first['offset'] == 0
    assert first['has_more'] is True and first['next_offset'] == 2
    assert [item['id'] for item in first['items']] == [1, 2]
    assert [item['id'] for item in second['items']] == [3, 4]
    assert [item['id'] for item in last['items']] == [5]
    assert last['has_more'] is False and last['next_offset'] is None
    assert [item['segment_count'] for item in first['items']] == [1, 1]
    internal = access.list_channels(catalog_db, role='train', limit=None)
    assert len(internal['items']) == internal['count'] == 5


def test_catalog_pagination_preserves_filters_and_role_isolation(catalog_db):
    result = access.list_channels(
        catalog_db, role='train', filters={'namespace': 'mission-A'}, limit=10, offset=0)
    assert result['count'] == 4
    assert {item['namespace'] for item in result['items']} == {'mission-A'}
    assert access.list_channels(catalog_db, role='test', limit=10)['count'] == 1
    assert access.list_channels(catalog_db, role='raw', limit=10)['count'] == 0


def test_time_filtered_catalog_keeps_exact_sample_window_then_pages(catalog_db):
    result = access.list_channels(
        catalog_db, role='train', start='2026-11-07 00:00:01',
        end='2026-11-07 00:00:02', limit=2, offset=2)
    assert result['count'] == 5
    assert [item['id'] for item in result['items']] == [3, 4]
    assert result['has_more'] is True


def test_catalog_pagination_validates_limit_and_offset(catalog_db):
    for kwargs in (
        {'limit': 0}, {'limit': 5001}, {'limit': True}, {'offset': -1},
        {'offset': True}, {'offset': 1.5},
    ):
        with pytest.raises(ValueError):
            access.list_channels(catalog_db, role='train', **kwargs)


def test_filter_options_uses_distinct_facets_not_full_channel_materialization(catalog_db, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('filter_options must not materialize list_channels')

    monkeypatch.setattr(access, 'list_channels', forbidden)
    result = access.filter_options(catalog_db, role='train')
    assert result['namespaces'] == ['mission-A', 'mission-B']
    assert result['models'] == ['SAT-A']
    assert result['subsystems'] == ['ADCS']
    assert result['devices'] == ['RW-1', 'RW-2', 'RW-3', 'RW-4', 'RW-5']
    assert result['variables'] == ['speed']


def test_http_catalog_route_accepts_limit_and_offset(catalog_db, tmp_path):
    class Main:
        ROOT = tmp_path

        @staticmethod
        def json_response(_handler, payload, status=200):
            return status, payload

    datasets = tmp_path / 'datasets'
    datasets.mkdir()
    (datasets / 'aas_telemetry_v2.sqlite').write_bytes(catalog_db.read_bytes())
    status, result = GET_DATA_ROUTES['/api/v2/telemetry/series/search'](
        None, urlparse('/api/v2/telemetry/series/search?role=train&limit=2&offset=2'), Main)

    assert status == 200
    assert result['count'] == 5
    assert [item['id'] for item in result['items']] == [3, 4]
    assert result['next_offset'] == 4


def test_http_catalog_route_rejects_invalid_pagination(catalog_db, tmp_path):
    class Main:
        ROOT = tmp_path

        @staticmethod
        def json_response(_handler, payload, status=200):
            return status, payload

    datasets = tmp_path / 'datasets'
    datasets.mkdir()
    (datasets / 'aas_telemetry_v2.sqlite').write_bytes(catalog_db.read_bytes())
    status, result = GET_DATA_ROUTES['/api/v2/telemetry/series/search'](
        None, urlparse('/api/v2/telemetry/series/search?role=train&limit=999'), Main)
    assert status == 400
    assert 'limit' in result['error']


def test_filter_options_apply_ancestor_filters_without_loading_catalog(catalog_db, monkeypatch):
    monkeypatch.setattr(access, 'list_channels', lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('filter_options must remain SQL distinct')))
    result = access.filter_options(catalog_db, role='train', filters={
        'namespace': 'mission-A', 'model': 'SAT-A', 'subsystem': 'ADCS'})
    assert result['namespaces'] == ['mission-A', 'mission-B']
    assert result['models'] == ['SAT-A']
    assert result['devices'] == ['RW-1', 'RW-2', 'RW-3', 'RW-4']


def test_http_filter_options_accepts_ancestor_filters(catalog_db, tmp_path):
    from scripts.server.data_routes import GET_DATA_ROUTES
    class Main:
        ROOT = tmp_path
        @staticmethod
        def json_response(_handler, payload, status=200):
            return status, payload
    datasets = tmp_path / 'datasets'
    datasets.mkdir()
    (datasets / 'aas_telemetry_v2.sqlite').write_bytes(catalog_db.read_bytes())
    status, result = GET_DATA_ROUTES['/api/v2/telemetry/filter-options'](
        None, urlparse('/api/v2/telemetry/filter-options?role=train&namespace=mission-A&model=SAT-A&subsystem=ADCS'), Main)
    assert status == 200
    assert result['devices'] == ['RW-1', 'RW-2', 'RW-3', 'RW-4']
