"""V2 browser contract: namespace, role, time, conflict and read-only isolation."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import pytest

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.server.telemetry_v2_access import filter_options, list_channels, open_database, query_charts
from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES


@pytest.fixture()
def v2(tmp_path):
    db=tmp_path/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript(CANON_SCHEMA)
        conn.execute('INSERT INTO v2_meta VALUES (?,?)',('schema_version','2'))
        for sid,namespace in ((1,'missionA'),(2,'missionB')):
            conn.execute('INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count) VALUES (?,?,?,?,?,?,?,?)',
                         (sid,namespace,namespace+'.db','x',10,'2026-10-20',4,6))
            conn.execute('INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit) VALUES (?,?,?,?,?,?,?)',
                         (sid,namespace,'GF1','ADCS','RW1','speed','rpm'))
        channels=[(1,1,'part_a',1,'train','aas_table_meta','datetime_naive',2,'2026-01-01 00:00:00','2026-01-01 00:00:01'),
                  (2,1,'part_b',1,'train','aas_table_meta','datetime_naive',2,'2026-01-01 00:00:02','2026-01-01 00:00:03'),
                  (3,1,'test_part',1,'test','aas_table_meta','datetime_naive',2,'2026-01-01 00:00:02','2026-01-01 00:00:03'),
                  (4,2,'mission_b',2,'train','aas_table_meta','datetime_naive',1,'2026-01-01 00:00:00','2026-01-01 00:00:00'),
                  (5,1,'hinted_test',1,'test','name_hint_only','datetime_naive',1,'2026-01-01 00:00:04','2026-01-01 00:00:04')]
        for sid,src,table,channel,role,provenance,domain,n,lo,hi in channels:
            conn.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(sid,src,table,channel,'telemetry',role,provenance,domain,1,'[]',n,lo,hi))
        for seg,channel,ordinal,stamp,value,label in [
            (1,1,0,'2026-01-01 00:00:00',1,0),(1,1,1,'2026-01-01 00:00:01',2,0),
            (2,1,0,'2026-01-01 00:00:02',3,0),(2,1,1,'2026-01-01 00:00:03',4,0),
            (3,1,0,'2026-01-01 00:00:02',9,1),(3,1,1,'2026-01-01 00:00:03',8,1),
            (4,2,0,'2026-01-01 00:00:00',100,0),(5,1,0,'2026-01-01 00:00:04',44,1)]:
            conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_text,value_num,value_raw_json,label_int,label_raw_json,raw_row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (seg,channel,ordinal,stamp,'str','datetime_naive',stamp.replace(' ','T')+'.000000',value,str(value),label,str(label),'{}'))
        conn.commit()
    return db


def test_catalog_returns_logical_channels_and_role_isolation(v2):
    result=list_channels(v2,role='train')
    assert result['count']==2  # not 3 source tables and not test partitions
    assert [(i['namespace'],i['segment_count']) for i in result['items']]==[('missionA',2),('missionB',1)]
    assert len(filter_options(v2,role='train')['namespaces'])==2
    assert list_channels(v2,role='test')['items'][0]['segment_count']==1
    assert list_channels(v2,role='test',filters={'namespace':'missionA','keyword':'spe'})['count']==1


def test_cross_partition_query_sorted_and_with_provenance(v2):
    result=query_charts(v2,role='train',channel_ids=[1],start='2026-01-01 00:00:01',end='2026-01-01 00:00:03',limit=10)['series'][0]
    assert [p['value'] for p in result['points']]==[2,3,4]
    assert [p['source_table'] for p in result['points']]==['part_a','part_b','part_b']
    assert result['segment_count']==2
    assert result['matched_count']==3
    assert query_charts(v2,role='test',channel_ids=[1])['series'][0]['points'][0]['value']==9
    assert query_charts(v2,role='train',channel_ids=[2])['series'][0]['points'][0]['value']==100


def test_window_filter_excludes_nonoverlapping_segments(v2):
    assert list_channels(v2,role='train',start='2026-02-01 00:00:00')['count']==0
    with pytest.raises(ValueError,match='开始时间'):
        query_charts(v2,role='train',channel_ids=[1],start='2026-02-01 00:00:00',end='2026-01-01 00:00:00')
    with pytest.raises(ValueError,match='无时区'):
        query_charts(v2,role='train',channel_ids=[1],start='2026-01-01T00:00:00Z')


def test_conflict_at_same_timestamp_refuses_silent_merge(v2):
    with sqlite3.connect(v2) as conn:
        conn.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                        VALUES (6,1,'conflict',1,'telemetry','train','aas_table_meta','datetime_naive',1,'[]',1,?,?)''',
                     ('2026-01-01 00:00:02','2026-01-01 00:00:02'))
        conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_text,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                        VALUES (6,1,0,'2026-01-01 00:00:02','str','datetime_naive','2026-01-01T00:00:02.000000',55,'55',0,'0','{}')''')
    with pytest.raises(ValueError,match='冲突'):
        query_charts(v2,role='train',channel_ids=[1])


def test_validation_and_read_only(v2):
    for kwargs in ({'role':'all','channel_ids':[1]}, {'role':'train','channel_ids':[]},
                   {'role':'train','channel_ids':[1,1]}, {'role':'train','channel_ids':['1']},
                   {'role':'train','channel_ids':[1],'limit':5001}):
        with pytest.raises(ValueError):query_charts(v2,**kwargs)
    with open_database(v2) as conn:
        with pytest.raises(sqlite3.OperationalError):conn.execute('DELETE FROM v2_channels')


def test_http_routes_v2(v2,tmp_path):
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,payload,status=200):
            return (status,payload)
    datasets=tmp_path/'datasets';datasets.mkdir();(datasets/'aas_telemetry_v2.sqlite').write_bytes(v2.read_bytes())
    status,catalog=GET_DATA_ROUTES['/api/v2/telemetry/series/search'](None,urlparse('/api/v2/telemetry/series/search?role=train&namespace=missionA'),Main)
    assert status==200 and catalog['count']==1
    status,result=POST_DATA_ROUTES['/api/v2/telemetry/query'](None,urlparse('/api/v2/telemetry/query'),Main,
                  json.dumps({'role':'train','channel_ids':[1],'limit':10}).encode())
    assert status==200 and len(result['series'][0]['points'])==4
    status,result=POST_DATA_ROUTES['/api/v2/telemetry/query'](None,urlparse('/api/v2/telemetry/query'),Main,b'{"role":"all"}')
    assert status==400


def test_page_uses_new_default_selection_layout():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/browser.html').read_text(encoding='utf-8')
    js=(root/'web/visualization/telemetry-v2-browser.js').read_text(encoding='utf-8')
    assert 'id="v2Candidates"' in html and 'id="v2Chart"' in html
    assert 'id="v2Browser"' in html and 'id="legacyBrowserTools"' not in html
    assert 'api/v2/telemetry/series/search' in js
    assert 'api/v2/telemetry/query' in js
    assert 'channel_ids' in js and 'source_table' in js


def test_unreviewed_train_test_source_splits_are_not_implicitly_fused(v2):
    # An existing V2 snapshot can contain names like DEMO_train_speed / DEMO_test_speed
    # whose roles were not explicitly certified at migration time.
    with sqlite3.connect(v2) as conn:
        conn.execute("UPDATE v2_segments SET role='unspecified', role_provenance='none',source_table='DEMO_train_speed' WHERE id=1")
        conn.execute("UPDATE v2_segments SET role='unspecified', role_provenance='none',source_table='DEMO_test_speed' WHERE id=2")
    catalog=list_channels(v2,role='unspecified')
    channel=next(item for item in catalog['items'] if item['id']==1)
    assert channel['unresolved_role_split']
    with pytest.raises(ValueError,match='未指定用途'):
        query_charts(v2,role='unspecified',channel_ids=[1])


def test_duplicate_identical_samples_dedup_without_losing_source_metadata(v2):
    with sqlite3.connect(v2) as conn:
        conn.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
                        VALUES (6,1,'identical',1,'telemetry','train','aas_table_meta','datetime_naive',1,'[]',1,?,?)''',
                     ('2026-01-01 00:00:02','2026-01-01 00:00:02'))
        conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_text,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                        VALUES (6,1,0,'2026-01-01 00:00:02','str','datetime_naive','2026-01-01T00:00:02.000000',3,'3',0,'0','{}')''')
    rows=query_charts(v2,role='train',channel_ids=[1],limit=20)['series'][0]
    assert rows['matched_count']==5
    assert rows['duplicate_count']==1
    assert len(rows['points'])==4


def test_api_fails_when_v2_database_is_missing(tmp_path):
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,payload,status=200):return status,payload
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/filter-options'](None,urlparse('/api/v2/telemetry/filter-options'),Main)
    assert status==400 and '未安装' in result['error']
