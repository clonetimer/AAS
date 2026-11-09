"""Phase 16: V2-native source hierarchy, side-effect-free preview, isolated save/delete."""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.v2_simulation_injection import (
    SIM_NAME, v2_source_catalog, preview_v2_injection,save_v2_injection,
    delete_injection,list_injections,preview_injection
)

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def source_root(tmp_path,monkeypatch):
    monkeypatch.delenv('AAS_V2_DB_FILENAME',raising=False)
    folder=tmp_path/'datasets';folder.mkdir()
    db=folder/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as con:
        con.executescript(CANON_SCHEMA)
        con.execute("INSERT INTO v2_meta(key,value) VALUES('schema_version','2')")
    rows='time_s,label,rw.speed,rw.current\n'+''.join(f'{i*2},0,{100+i*0.1},{1+i*0.002}\n' for i in range(620))
    result=import_wide_csv(db,{
        'namespace':'healthy_test','model':'SIM-SAT-X','satellite_instance':'',
        'subsystem':'adcs','device':'reaction_wheel','variable':'ignored','unit':'rad/s',
        'role':'train','time_domain':'numeric_unspecified','time_reference':'run:x',
        'time_unit':'s','filename':'healthy.csv','csv':rows,
        'column_metadata':{'rw.speed':{'subsystem':'adcs','device':'reaction_wheel','variable':'speed','unit':'rad/s'},
                           'rw.current':{'subsystem':'eps','device':'rw_driver','variable':'current','unit':'A'}}
    },commit=True)
    return tmp_path,db,result


def params(catalog, *,onset=400):
    x=next(r for r in catalog['items'] if r['variable']=='speed')
    return {'source_id':x['source_id'],'channel_id':x['channel_id'],'fault_type':'bias',
            'onset_s':onset,'magnitude':18,'max_points':600}


def test_catalog_hierarchy_and_pure_preview(source_root):
    root,db,_=source_root
    catalog=v2_source_catalog(root)
    assert {(r['model'],r['subsystem'],r['device'],r['variable']) for r in catalog['items']}=={
        ('SIM-SAT-X','adcs','reaction_wheel','speed'),('SIM-SAT-X','eps','rw_driver','current')}
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    first=preview_v2_injection(root,params(catalog))
    assert len(first['points'])==600
    assert first['fault_rows']==400
    assert first['points'][200]['original']!=first['points'][200]['value']
    assert before==hashlib.sha256(db.read_bytes()).hexdigest()
    assert not (root/'datasets'/SIM_NAME).exists()
    assert first['preview_sha256']==preview_v2_injection(root,params(catalog))['preview_sha256']


def test_save_requires_preview_fingerprint_and_delete_is_isolated(source_root):
    root,db,_=source_root
    catalog=v2_source_catalog(root)
    config=params(catalog)
    preview=preview_v2_injection(root,config)
    with pytest.raises(ValueError,match='先预览'):
        save_v2_injection(root,config)
    with pytest.raises(ValueError,match='重新预览'):
        save_v2_injection(root,{**config,'magnitude':99,'preview_sha256':preview['preview_sha256']})
    original_hash=hashlib.sha256(db.read_bytes()).hexdigest()
    saved=save_v2_injection(root,{**config,'preview_sha256':preview['preview_sha256']})
    assert saved['status']=='success' and saved['source_database_copied'] is False
    new=save_v2_injection(root,{**config,'preview_sha256':preview['preview_sha256']})
    sim=root/'datasets'/SIM_NAME
    with sqlite3.connect(sim) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert con.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==1200
        assert con.execute('SELECT COUNT(*) FROM v2_injection_records').fetchone()[0]==2
    assert len(preview_injection(root,saved['id'])['points'])==600
    result=delete_injection(root,saved['id'])
    assert result['status']=='deleted' and result['deleted_segments']==1
    with sqlite3.connect(sim) as con:
        assert con.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==600
        assert con.execute('SELECT COUNT(*) FROM v2_injection_records').fetchone()[0]==1
        assert con.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert len(preview_injection(root,new['id'])['points'])==600
    assert original_hash==hashlib.sha256(db.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='不存在'):
        delete_injection(root,saved['id'])


def test_preinstalled_demo_record_removal_preserves_other_records(tmp_path):
    root=tmp_path
    folder=root/'datasets';folder.mkdir()
    shutil.copy2(ROOT/'datasets'/SIM_NAME,folder/SIM_NAME)
    before=list_injections(root)['items']
    assert before
    other=before[1:]
    delete_injection(root,before[0]['id'])
    now=list_injections(root)['items']
    assert len(now)==len(before)-1
    assert [i['id'] for i in now]==[i['id'] for i in other]


def test_ui_uses_hierarchical_filters_separate_preview_save_and_delete():
    html=(ROOT/'web/visualization/simulation.html').read_text(encoding='utf-8')
    js=(ROOT/'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    for name in ('simModel','simSubsystem','simDevice','simColumn','simSource','simDryRun','simSave'):
        assert f'id="{name}"' in html
    assert 'simToken' not in html and 'simCreate' not in html
    for route in ('/api/v2/simulation/sources','/api/v2/simulation/dry-run',
                  '/api/v2/simulation/save','/api/v2/simulation/remove'):
        assert route in js
    assert 'window.confirm' in js


def test_real_http_simulation_preview_save_and_delete(source_root,monkeypatch):
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request,urlopen
    import scripts.visualization_server as main
    from tests.http_test_handler import AuthorizedTestHandler
    root,db,_=source_root
    monkeypatch.setattr(main,'ROOT',root)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def send(path,data=None):
        body=json.dumps(data).encode() if data is not None else None
        request=Request(base+path,data=body,method='POST' if body else 'GET',
                        headers={'Content-Type':'application/json'} if body else {})
        with urlopen(request,timeout=8) as response:
            assert response.status==200
            return json.loads(response.read())
    try:
        catalog=send('/api/v2/simulation/sources')
        cfg=params(catalog)
        before=hashlib.sha256(db.read_bytes()).hexdigest()
        result=send('/api/v2/simulation/dry-run',cfg)
        assert result['fault_rows']==400
        assert not (root/'datasets'/SIM_NAME).exists()
        saved=send('/api/v2/simulation/save',{**cfg,'preview_sha256':result['preview_sha256']})
        assert send('/api/v2/simulation/list')['items'][0]['id']==saved['id']
        assert send('/api/v2/simulation/remove',{'id':saved['id']})['status']=='deleted'
        assert send('/api/v2/simulation/list')['items']==[]
        assert before==hashlib.sha256(db.read_bytes()).hexdigest()
    finally:
        server.shutdown();worker.join(timeout=5);server.server_close()
