"""Browsing tolerates independent time windows without loosening model contracts."""
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import pytest

from tests.test_telemetry_v2_browser_phase4 import v2
from scripts.server.telemetry_v2_access import channel_time_coverage, list_channels, query_charts
from scripts.server.data_routes import GET_DATA_ROUTES


def _move_channel2_later(db):
    with sqlite3.connect(db) as con:
        # The UI does not implicitly equate naive wall-clock coordinates from
        # different namespaces. Put both channels in the same run namespace.
        con.execute("UPDATE v2_channels SET namespace='missionA',variable='speed_secondary' WHERE id=2")
        con.execute("UPDATE v2_segments SET time_start_raw='2026-01-01 00:00:10', time_end_raw='2026-01-01 00:00:10' WHERE id=4")
        con.execute("UPDATE v2_samples SET timestamp_raw='2026-01-01 00:00:10', time_sort_text='2026-01-01T00:00:10.000000' WHERE segment_id=4")


def test_union_browse_window_allows_nonoverlapping_same_time_domain(v2):
    _move_channel2_later(v2)
    old = channel_time_coverage(v2, role='train', channel_ids=[1,2])
    assert old['has_common_window'] is False
    view = channel_time_coverage(v2, role='train', channel_ids=[1,2], mode='union')
    assert view['has_common_window'] is True
    assert view['min'].startswith('2026-01-01T00:00:00')
    assert view['max'].startswith('2026-01-01T00:00:10')
    assert view['coverage_mode'] == 'union'
    lines = query_charts(v2, role='train', channel_ids=[1,2])['series']
    assert len(lines) == 2 and lines[1]['points'][0]['value'] == 100


def test_union_browse_keeps_time_domain_checks(v2):
    with sqlite3.connect(v2) as con:
        con.execute("UPDATE v2_segments SET time_domain='numeric_unspecified',time_start_raw='10',time_end_raw='10' WHERE id=4")
        con.execute("UPDATE v2_samples SET time_sort_num=10 WHERE segment_id=4")
    with pytest.raises(ValueError, match='不同时间域'):
        channel_time_coverage(v2, role='train', channel_ids=[1,2], mode='union')
    with pytest.raises(ValueError):
        channel_time_coverage(v2, role='train', channel_ids=[1], mode='arbitrary')


def test_http_union_mode_and_unverified_browse_role(v2,tmp_path):
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,payload,status=200):return status,payload
    datasets=tmp_path/'datasets'; datasets.mkdir()
    file=datasets/'aas_telemetry_v2.sqlite';file.write_bytes(v2.read_bytes())
    _move_channel2_later(file)
    url='/api/v2/telemetry/series/coverage?role=train&channel_ids=1,2&mode=union'
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/series/coverage'](None,urlparse(url),Main)
    assert status==200 and result['coverage_mode']=='union'
    url='/api/v2/telemetry/series/search?role=unspecified'
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/series/search'](None,urlparse(url),Main)
    assert status==200 and result['count']==0


def test_browser_exposes_read_only_uncertified_sources_and_union_mode():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/browser.html').read_text('utf-8')
    js=(root/'web/visualization/telemetry-v2-browser.js').read_text('utf-8')
    for role in ('raw','unspecified','simulation','processed','simulated_fault'):
        assert f'<option value="{role}">' in html
    assert 'mode:showBrowseUnion' in js
    assert '浏览不代表' in html
    assert '已显示 ${total} 帧' in js
    assert '跨来源帧号不可直接视作统一连续序号' in html
