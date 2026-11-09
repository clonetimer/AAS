from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_access import query_charts
from scripts.server import data_routes


def chart_db(tmp_path: Path, frames: int = 500) -> Path:
    db = tmp_path / 'chart.sqlite'
    with sqlite3.connect(db) as conn:
        create_v2(conn)
        conn.execute(
            '''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
               imported_utc,source_table_count,source_row_count) VALUES (1,'mission-A','source.csv','sha',100,'2026-11-07',2,?)''',
            (frames + 1,))
        conn.execute(
            '''INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit)
               VALUES (1,'mission-A','SAT-A','ADCS','RW-1','speed','rpm')''')
        conn.execute(
            '''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
               role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,
               time_start_raw,time_end_raw) VALUES (1,1,'part-a',1,'telemetry','train',
               'aas_table_meta','numeric_unspecified',1,'[]',?,?,?)''',
            (frames, '0', str(frames - 1)))
        rows = []
        for index in range(frames):
            value = None if index % 50 == 0 else float(index % 13)
            if index == 245:
                value = 10000.0
            elif index == 410:
                value = -10000.0
            label = 1 if index in (301, 302, 450) else 0
            rows.append((1, 1, index, str(index), 'str', 'numeric_unspecified',
                         float(index), value, str(value), label, str(label), '{}'))
        conn.executemany(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
        # An identical duplicate must collapse to one logical frame.
        conn.execute(
            '''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
               role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,
               time_start_raw,time_end_raw) VALUES (2,1,'part-b',1,'telemetry','train',
               'aas_table_meta','numeric_unspecified',1,'[]',1,'10','10')''')
        conn.execute(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (2,1,0,'10','str','numeric_unspecified',10,10.0,'10',0,'0','{}')''')
        conn.commit()
    return db


def test_overview_covers_full_range_and_reports_distinct_frame_metrics(tmp_path):
    db = chart_db(tmp_path)
    series = query_charts(db, role='train', channel_ids=[1], limit=100)['series'][0]

    assert series['matched_count'] == 501
    assert series['frame_count'] == 500
    assert series['valid_sample_count'] == 490
    assert series['duplicate_count'] == 1
    assert series['plot_point_count'] == len(series['points'])
    assert 0 < series['plot_point_count'] <= 100
    assert series['truncated'] is True
    assert series['coverage']['start_key'] == 0.0
    assert series['coverage']['end_key'] == 499.0
    values = [point['value'] for point in series['points']]
    assert max(values) == 10000.0
    assert min(values) == -10000.0
    assert any(point['label'] == 1 for point in series['points'])
    assert series['points'][0]['time_key'] == 1.0
    assert series['points'][-1]['time_key'] == 499.0


def test_small_window_returns_all_valid_points_and_counts_invalid_frames(tmp_path):
    db = chart_db(tmp_path, frames=40)
    series = query_charts(
        db, role='train', channel_ids=[1], start=0, end=10, limit=100)['series'][0]

    assert series['frame_count'] == 11
    assert series['valid_sample_count'] == 10
    assert series['plot_point_count'] == 10
    assert series['truncated'] is False
    assert [point['time_key'] for point in series['points']] == [
        1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def test_conflicting_duplicate_after_preview_limit_is_rejected(tmp_path):
    db = chart_db(tmp_path, frames=500)
    with sqlite3.connect(db) as conn:
        conn.execute(
            '''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
               timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
               label_raw_json,raw_row_json) VALUES (2,1,1,'499','str','numeric_unspecified',499,12345,'12345',0,'0','{}')''')

    with pytest.raises(ValueError, match='冲突采样'):
        query_charts(db, role='train', channel_ids=[1], limit=10)


def test_chart_overview_validates_limit_and_mode(tmp_path):
    db = chart_db(tmp_path, frames=10)
    for limit in (0, 5001, True):
        with pytest.raises(ValueError):
            query_charts(db, role='train', channel_ids=[1], limit=limit)
    with pytest.raises(ValueError, match='overview'):
        query_charts(db, role='train', channel_ids=[1], cursor='bad')


def test_http_chart_query_exposes_overview_metrics(tmp_path, monkeypatch):
    db = chart_db(tmp_path, frames=40)
    monkeypatch.setattr(data_routes, '_browser_db', lambda _main, _name: db)

    class Main:
        @staticmethod
        def json_response(_handler, payload, status=200):
            return status, payload

    status, payload = data_routes.POST_DATA_ROUTES['/api/v2/telemetry/query'](
        None, SimpleNamespace(path='/api/v2/telemetry/query'), Main,
        json.dumps({'role': 'train', 'channel_ids': [1], 'limit': 100,
                    'mode': 'overview'}).encode('utf-8'))

    assert status == 200
    series = payload['series'][0]
    assert series['frame_count'] == 40
    assert series['valid_sample_count'] == 39
    assert series['plot_point_count'] == 39
