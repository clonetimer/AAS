"""V2-native import contract: no legacy tables, no role/time mixing, no partial commits."""
import sqlite3
from pathlib import Path

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_access import query_charts, list_channels


def database(tmp_path):
    path = tmp_path / 'telemetry.sqlite'
    with sqlite3.connect(path) as conn:
        create_v2(conn)
    return path


def payload(**changes):
    item = dict(namespace='mission_A',satellite_instance='sat_001',model='GF1',subsystem='ADCS',
                device='RW1',variable='speed',unit='rpm',role='raw',time_domain='datetime_utc',
                filename='part1.csv',csv='timestamp,value,label\n2026-01-01T00:00:00Z,1,0\n2026-01-01T00:00:01Z,2,0\n')
    item.update(changes)
    return item


def counts(path):
    with sqlite3.connect(path) as conn:
        return tuple(conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in
                     ('v2_channels','v2_segments','v2_samples','v2_sources'))


def test_preview_does_not_write_and_two_parts_form_one_channel(tmp_path):
    path=database(tmp_path)
    original=counts(path)
    ready=import_csv(path,payload())
    assert ready['status']=='preview' and ready['row_count']==2
    assert counts(path)==original
    part1=import_csv(path,payload(),commit=True)
    part2=import_csv(path,payload(filename='part2.csv',csv='timestamp,value,label\n2026-01-01T00:00:02Z,3,0\n'),commit=True)
    assert part1['channel_id']==part2['channel_id']
    assert counts(path)==(1,2,3,2)
    search=list_channels(path,role='raw')
    assert len(search['items'])==1 and search['items'][0]['segment_count']==2
    series=query_charts(path,role='raw',channel_ids=[part1['channel_id']],limit=5)['series'][0]
    assert [p['value'] for p in series['points']]==[1,2,3]
    assert len(series['source_tables'])==2


def test_train_guard_and_test_split_are_independent(tmp_path):
    path=database(tmp_path)
    training=payload(role='train')
    import_csv(path,training,commit=True)
    with pytest.raises(ValueError,match='故障标签'):
        import_csv(path,payload(role='train',filename='bad.csv',csv='timestamp,value,label\n2026-01-01T00:00:03Z,4,1\n'),commit=True)
    result=import_csv(path,payload(role='test',filename='test.csv',csv='timestamp,value,label\n2026-01-01T00:00:00Z,7,1\n'),commit=True)
    assert result['role']=='test'
    assert counts(path)==(1,2,3,2)
    assert query_charts(path,role='train',channel_ids=[result['channel_id']])['series'][0]['matched_count']==2
    assert query_charts(path,role='test',channel_ids=[result['channel_id']])['series'][0]['matched_count']==1


def test_overlap_rejected_atomic_and_exact_duplicates_in_file(tmp_path):
    path=database(tmp_path)
    import_csv(path,payload(),commit=True)
    original=counts(path)
    with pytest.raises(ValueError,match='重叠时间戳'):
        import_csv(path,payload(filename='new.csv',csv='timestamp,value,label\n2026-01-01T00:00:01Z,99,1\n'),commit=True)
    assert counts(path)==original
    with pytest.raises(ValueError,match='重复'):
        import_csv(path,payload(csv='timestamp,value,label\n2026-01-01T00:00:04Z,1,0\n2026-01-01T00:00:04+00:00,1,0\n'),commit=True)
    assert counts(path)==original


def test_numeric_timebase_isolation_and_required_units(tmp_path):
    path=database(tmp_path)
    numeric=payload(time_domain='numeric_unspecified',time_reference='run_A_elapsed',time_unit='s',
                    csv='timestamp,value,label\n0,1,0\n1,2,0\n')
    import_csv(path,numeric,commit=True)
    import_csv(path,dict(numeric,filename='other.csv',csv='timestamp,value,label\n2,3,0\n'),commit=True)
    with pytest.raises(ValueError,match='基准'):
        import_csv(path,dict(numeric,filename='wrong.csv',time_reference='run_B_elapsed',csv='timestamp,value,label\n3,4,0\n'),commit=True)
    with pytest.raises(ValueError,match='基准'):
        import_csv(path,dict(numeric,filename='wrong.csv',time_unit='ms',csv='timestamp,value,label\n3,4,0\n'),commit=True)
    c=list_channels(path,role='raw')['items'][0]
    assert len(query_charts(path,role='raw',channel_ids=[c['id']])['series'][0]['points'])==3


def test_explicit_namespace_and_unit_guard(tmp_path):
    path=database(tmp_path)
    import_csv(path,payload(),commit=True)
    with pytest.raises(ValueError,match='单位'):
        import_csv(path,payload(unit='rad/s',filename='a.csv',csv='timestamp,value,label\n2026-01-01T00:00:04Z,5,0\n'),commit=True)
    separate=import_csv(path,payload(namespace='mission_B',filename='b.csv'),commit=True)
    assert len(list_channels(path,role='raw')['items'])==2
    assert separate['channel_id']!=1


def test_no_implicit_db_creation_and_invalid_time_ref(tmp_path):
    path=tmp_path/'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        import_csv(path,payload(),commit=True)
    path=database(tmp_path)
    with pytest.raises(ValueError,match='时基|数值时间基准'):
        import_csv(path,payload(time_domain='numeric_unspecified',time_unit='s',csv='timestamp,value,label\n0,1,0\n'))
    with pytest.raises(ValueError,match='时间域'):
        import_csv(path,payload(csv='timestamp,value,label\n2026-01-01 00:00:00,1,0\n'))
    assert counts(path)==(0,0,0,0)


def test_import_schema_leaves_legacy_record_buckets_unchanged(tmp_path):
    path=database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO v2_meta VALUES ('audit','preserve')")
    import_csv(path,payload(),commit=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM v2_meta WHERE key='audit'").fetchone()[0]=='preserve'
        assert conn.execute('SELECT COUNT(*) FROM v2_legacy_rows').fetchone()[0]==0
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_import_api_preview_commit_and_history_route(tmp_path):
    import json
    from urllib.parse import urlparse
    from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES
    datasets=tmp_path/'datasets'
    datasets.mkdir()
    path=database(tmp_path)
    path.rename(datasets/'aas_telemetry_v2.sqlite')
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler, result, status=200):
            return status,result
    req=json.dumps(payload(),ensure_ascii=False).encode()
    status,preview=POST_DATA_ROUTES['/api/v2/telemetry/import-csv'](
        None,urlparse('/api/v2/telemetry/import-csv'),Main,req)
    assert status==200 and preview['status']=='preview'
    status,written=POST_DATA_ROUTES['/api/v2/telemetry/import-csv'](
        None,urlparse('/api/v2/telemetry/import-csv'),Main,
        json.dumps({**payload(),'commit':True}).encode())
    assert status==200 and written['status']=='imported'
    status,history=GET_DATA_ROUTES['/api/v2/telemetry/imports'](
        None,urlparse('/api/v2/telemetry/imports'),Main)
    assert status==200 and history['count']==1
    assert history['items'][0]['segment_id']==written['segment_id']
    status,blocked=POST_DATA_ROUTES['/api/v2/telemetry/import-csv'](
        None,urlparse('/api/v2/telemetry/import-csv'),Main,
        json.dumps({**payload(),'commit':True}).encode())
    assert status==400 and '重叠' in blocked['error']
    status,visible=GET_DATA_ROUTES['/api/v2/telemetry/series/search'](
        None,urlparse('/api/v2/telemetry/series/search?role=raw'),Main)
    assert status==200 and visible['count']==1


def test_preview_does_not_create_extension_metadata(tmp_path):
    path=database(tmp_path)
    import_csv(path,payload())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='v2_segment_clock'").fetchone() is None


def test_source_ordinals_survive_global_chronological_sort(tmp_path):
    path=database(tmp_path)
    result=import_csv(path,payload(csv='timestamp,value,label\n2026-01-01T00:00:02Z,2,0\n2026-01-01T00:00:01Z,1,0\n'),commit=True)
    points=query_charts(path,role='raw',channel_ids=[result['channel_id']])['series'][0]['points']
    assert [(p['value'],p['source_ordinal']) for p in points]==[(1.,1),(2.,0)]


def test_numeric_precision_must_not_collapse_distinct_samples(tmp_path):
    path=database(tmp_path)
    with pytest.raises(ValueError,match='精度'):
        import_csv(path,payload(time_domain='numeric_unspecified',time_reference='clock_A',time_unit='ns',
            csv='timestamp,value,label\n9007199254740993,1,0\n'),commit=True)
    assert counts(path)==(0,0,0,0)


def test_numeric_multi_channel_plot_can_compare_independent_run_clocks(tmp_path):
    path=database(tmp_path)
    shared=dict(time_domain='numeric_unspecified',time_reference='run_A',time_unit='s',
                csv='timestamp,value,label\n0,1,0\n1,2,0\n')
    a=import_csv(path,payload(**shared),commit=True)
    b=import_csv(path,payload(**{**shared,'variable':'current','unit':'A','filename':'current.csv',
                                      'csv':'timestamp,value,label\n0,0.1,0\n1,0.2,0\n'}),commit=True)
    assert len(query_charts(path,role='raw',channel_ids=[a['channel_id'],b['channel_id']])['series'])==2
    c=import_csv(path,payload(**{**shared,'variable':'temperature','unit':'C',
                                      'filename':'temperature.csv','time_reference':'run_B',
                                      'csv':'timestamp,value,label\n0,20,0\n1,21,0\n'}),commit=True)
    # Since Phase 12 this is an explicit relative-time visual comparison; sources remain separate.
    compared=query_charts(path,role='raw',channel_ids=[a['channel_id'],c['channel_id']])
    assert len(compared['series'])==2
    assert {v['channel']['id'] for v in compared['series']}=={a['channel_id'],c['channel_id']}
