"""New V2-only presentation and subset contracts (no legacy table UI)."""
from __future__ import annotations
import csv
import gzip
import sqlite3
from pathlib import Path
import pytest
from scripts.build_uploaded_v2 import build
from scripts.server.telemetry_v2_access import query_charts, list_channels, display_time, relative_seconds
from scripts.server.v2_database_config import list_databases,resolve_database
from scripts.migrate_v2_calendar_view import install
from scripts.server.v2_run_dataset import _parse_selection

@pytest.fixture
def small_db(tmp_path):
    incoming=tmp_path/'source';incoming.mkdir()
    for run in ['sat_a','sat_b']:
        with gzip.open(incoming/f'{run}.csv.gz','wt',encoding='utf-8',newline='') as f:
            writer=csv.writer(f);writer.writerow(['time_s','adcs.rw.speed_rad_s'])
            writer.writerows([[i,float(i)] for i in range(6)])
    ds=tmp_path/'datasets';ds.mkdir()
    path=ds/'example.db';build(incoming,path)
    return tmp_path,path

def test_v2_calendar_view_and_reversible_display(small_db):
    root,path=small_db
    assert display_time(3600.25)=='2026-01-01 01:00:00.250'
    assert relative_seconds('2026-01-01 01:00:00.250')==3600.25
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT display_timestamp FROM v2_samples_calendar WHERE time_sort_num=0 LIMIT 1').fetchone()[0]=='2026-01-01 00:00:00.000'
    assert install(path)==12
    assert list_databases(root)==['example.db']
    assert resolve_database(root,'example.db')==path
    with pytest.raises(ValueError):resolve_database(root,'../another.db')

def test_two_satellites_overlay_without_merging(small_db):
    _,path=small_db
    channels=list_channels(path,role='simulation')['items']
    assert len(channels)==2
    result=query_charts(path,role='simulation',channel_ids=[c['id'] for c in channels],start='2026-01-01 00:00:02',end='2026-01-01 00:00:04',limit=10)
    assert [len(c['points']) for c in result['series']]==[3,3]
    assert all(x['points'][0]['timestamp']=='2026-01-01 00:00:02.000' for x in result['series'])
    assert len({x['channel']['satellite_instance'] for x in result['series']})==2

def test_dates_map_back_to_run_subset_seconds():
    ids,keys,start,end,method,tol=_parse_selection({'source_ids':[1,2], 'feature_keys':['rw.speed'],
        'start':'2026-01-01 00:00:03','end':'2026-01-01 00:00:09','method':'exact',
        'tolerance_seconds':0},role='train')
    assert (start,end)==(3,9)

def test_formal_pages_are_v2_only():
    base=Path(__file__).resolve().parents[1]/'web'/'visualization'
    browser=(base/'browser.html').read_text(encoding='utf-8')
    runner=(base/'runner.html').read_text(encoding='utf-8')
    chart=(base/'telemetry-v2-browser.js').read_text(encoding='utf-8')
    assert 'id="v2Database"' in browser
    assert all(k in browser for k in ('v2ZoomIn','v2ZoomOut','v2ZoomReset'))
    assert '来源数据集' not in browser
    assert '① 选择训练运行' not in runner
    assert '① 筛选训练数据' in runner
    assert '② 选择算法' in runner
    assert '③ 配置参数' in runner
    assert '④ 执行训练' in runner
    assert 'id="trainAdvancedParams"' in runner
    assert 'model_params' not in browser
    assert '不同参量的数字时钟基准' not in chart
