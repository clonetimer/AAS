"""Phase 27: browser-selected V2 CSV export is complete, scoped and read-only."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_selection_export import build_selection_export
from scripts.server.telemetry_v2_access import query_charts

ROOT = Path(__file__).resolve().parents[1]


def _database(tmp_path):
    db=tmp_path/'datasets'/'aas_telemetry_v2.sqlite'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        create_v2(conn)
    return db


def _load(db, *,role='train',variable='speed',model='SAT-001',namespace='mission',filename='speed.csv',length=3700,offset=0):
    data='timestamp,value,label\n'+''.join(f'{j},{j + (100 if role=="test" else 0)},{1 if role=="test" else 0}\n' for j in range(offset,offset+length))
    return import_csv(db, dict(namespace=namespace,satellite_instance='',model=model,subsystem='adcs',device='rw1',variable=variable,unit='rad/s',role=role,time_domain='numeric_unspecified',time_reference='run-1',time_unit='s',filename=filename,csv=data),commit=True)


def _read(stream):
    try:
        return list(csv.DictReader(io.TextIOWrapper(stream,encoding='utf-8-sig',newline='')))
    finally:
        stream.close()


def test_export_full_samples_not_chart_cap(tmp_path):
    db=_database(tmp_path)
    cid=_load(db)['channel_id']
    plotted=query_charts(db,role='train',channel_ids=[cid],limit=3000)['series'][0]
    assert 0 < len(plotted['points']) <= 3000 and plotted['truncated']
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    stream, content_type, filename=build_selection_export(db,role='train',channel_ids=[cid])
    rows=_read(stream)
    assert len(rows)==3700
    assert rows[0]['timestamp']=='2026-01-01 00:00:00.000'
    assert rows[-1]['relative_seconds']=='3699.0'
    assert rows[0]['source_table']=='speed.csv'
    assert rows[0]['satellite_model']=='SAT-001'
    assert content_type.startswith('text/csv') and filename.endswith('.csv')
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_time_range_roles_and_multiple_satellites_not_joined(tmp_path):
    db=_database(tmp_path)
    a=_load(db,length=10)['channel_id']
    b=_load(db,model='SAT-002',namespace='mission_two',filename='sat2.csv',length=10)['channel_id']
    _load(db,role='test',filename='test.csv',length=10)
    stream,_,_=build_selection_export(db,role='train',channel_ids=[a,b],start='2026-01-01 00:00:03',end='2026-01-01 00:00:06')
    rows=_read(stream)
    assert len(rows)==8
    assert [r['satellite_model'] for r in rows]==['SAT-001']*4+['SAT-002']*4
    assert {r['relative_seconds'] for r in rows}=={'3.0','4.0','5.0','6.0'}
    assert {r['data_role'] for r in rows}=={'train'}
    stream,_,_=build_selection_export(db,role='test',channel_ids=[a])
    assert len(_read(stream))==10


def test_invalid_selection_rejected_and_empty_time_window_reported(tmp_path):
    db=_database(tmp_path)
    cid=_load(db,length=3)['channel_id']
    for ids in ([],[cid,cid],['1'],[0]):
        with pytest.raises(ValueError):build_selection_export(db,role='train',channel_ids=ids)
    with pytest.raises(ValueError,match='开始时间'):
        build_selection_export(db,role='train',channel_ids=[cid],start=10,end=2)
    with pytest.raises(ValueError,match='没有可导出'):
        build_selection_export(db,role='train',channel_ids=[cid],start=100,end=200)


def test_conflicting_timestamp_refused_in_export(tmp_path):
    db=_database(tmp_path)
    cid=_load(db,length=3)['channel_id']
    _load(db,filename='later.csv',offset=3,length=1)
    with sqlite3.connect(db) as conn:
        segment=conn.execute("SELECT id FROM v2_segments WHERE source_table='later.csv'").fetchone()[0]
        conn.execute('UPDATE v2_samples SET time_sort_num=2,timestamp_raw=\'2\',value_num=999 WHERE segment_id=?',(segment,))
    with pytest.raises(ValueError,match='冲突采样'):
        build_selection_export(db,role='train',channel_ids=[cid])


def test_http_download_and_form_contract(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request,urlopen
    from tests.http_test_handler import AuthorizedTestHandler
    import scripts.visualization_server as main
    db=_database(tmp_path)
    cid=_load(db,length=5)['channel_id']
    monkeypatch.setattr(main,'ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        body=json.dumps({'database':db.name,'role':'train','channel_ids':[cid], 'start':'2026-01-01 00:00:01','end':'2026-01-01 00:00:03'}).encode()
        with urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/v2/telemetry/export-selection',data=body,headers={'Content-Type':'application/json'}),timeout=15) as response:
            result=response.read()
            assert response.status==200
            assert 'attachment' in response.headers['Content-Disposition']
        rows=list(csv.DictReader(io.StringIO(result.decode('utf-8-sig'))))
        assert len(rows)==3
        assert rows[-1]['value']=='3.0'
    finally:
        server.shutdown();server.server_close();thread.join(3)
    html=(ROOT/'web/visualization/browser.html').read_text(encoding='utf-8')
    js=(ROOT/'web/visualization/telemetry-v2-browser.js').read_text(encoding='utf-8')
    assert html.count('id="v2Export"')==1
    assert html.index('id="v2ZoomReset"')<html.index('id="v2Export"')<html.index('id="v2Chart"')
    assert '/api/v2/telemetry/export-selection' in js
    assert 'AAS_V2_telemetry_loaded.csv' not in js
