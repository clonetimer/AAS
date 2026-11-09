"""P49 local batch control: review, isolation, durable status and HTTP UI contract."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import time
import zipfile

import pytest
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server import telemetry_v2_batch_tasks as batch


def fixture(tmp_path,monkeypatch,*,normal=False):
    root=tmp_path/'admin_inbox';data=root/'runs';data.mkdir(parents=True)
    app=tmp_path/'app';(app/'datasets').mkdir(parents=True)
    with sqlite3.connect(app/'datasets'/'aas_telemetry_v2.sqlite') as db:create_v2(db)
    token='a'*24;group='b'*24
    kind='NORMAL' if normal else 'FAULT'
    meta={'token':token,'split':'train' if normal else 'validation','group':group,'role':kind,
          'fault':None if normal else 'wheel_degradation',
          'onset_s':None if normal else 1.0,'component':None if normal else 'adcs.rw',
          'metadata_not_model_features':True}
    with zipfile.ZipFile(root/'targets.zip','w') as z:z.writestr(f'targets/{token}.json',json.dumps(meta))
    (data/(token+'.csv')).write_text('timestamp,eps.battery_energy_j\n0,10\n1,12\n2,13\n',encoding='utf-8')
    monkeypatch.setenv('AAS_BATCH_IMPORT_ROOT',str(root))
    return app,root,data,token


def wait(app,task_id):
    for _ in range(150):
        out=batch.status(app,task_id)
        if out['status']!='running':return out
        time.sleep(.035)
    pytest.fail('batch worker did not finish')


def cfg():
    return {'folder':'runs','targets_archive':'targets.zip','model':'S',
            'unit_overrides':{},'confirm_normal':False}


def test_run_review_and_idempotent_retry(tmp_path,monkeypatch):
    app,root,data,token=fixture(tmp_path,monkeypatch)
    ws=batch.workspace();assert ws['enabled'] and 'runs' in ws['folders']
    reviewed=batch.preview(app,cfg())
    assert reviewed['ready'] and reviewed['count']==1
    assert batch.status(app,reviewed['task_id'])['status']=='previewed'
    running=batch.start(app,reviewed['task_id'])
    result=wait(app,running['task_id']);assert result['status']=='complete',result
    assert result['counts']['imported']==1
    with sqlite3.connect(app/'datasets'/'aas_telemetry_v2.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==3
        assert db.execute('SELECT dataset_split FROM v2_import_context').fetchone()[0]=='validation'
        assert db.execute('SELECT DISTINCT label_int FROM v2_samples').fetchone()[0] is None
    other=batch.preview(app,cfg())
    skipped=wait(app,batch.start(app,other['task_id'])['task_id'])
    assert skipped['counts']['skipped']==1
    assert len(batch.history(app)['items'])>=2


def test_unconfirmed_normal_training_is_blocked(tmp_path,monkeypatch):
    app,_,_,_=fixture(tmp_path,monkeypatch,normal=True)
    data=batch.preview(app,cfg())
    assert data['ready'] is False and data['unconfirmed_normal_files']==1
    reviewed=batch.preview(app,{**cfg(),'confirm_normal':True})
    assert reviewed['ready']
    out=wait(app,batch.start(app,reviewed['task_id'])['task_id'])
    assert out['status']=='complete' and out['counts']['imported']==1


def test_preview_missing_units_does_not_create_task(tmp_path,monkeypatch):
    app,_,data,token=fixture(tmp_path,monkeypatch)
    (data/(token+'.csv')).write_text('timestamp,propulsion.burn_commanded\n0,1\n1,0\n',encoding='utf-8')
    view=batch.preview(app,cfg());assert view['ready'] is False
    assert view['missing_units']==['propulsion.burn_commanded']
    assert batch.preview(app,{**cfg(),'unit_overrides':{'propulsion.burn_commanded':'1'}})['ready']


def test_path_escape_symlink_and_stale_source_denied(tmp_path,monkeypatch):
    app,root,data,token=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError):batch.preview(app,{**cfg(),'folder':'../'})
    with pytest.raises(ValueError):batch.preview(app,{**cfg(),'targets_archive':'../../secret.zip'})
    try:(root/'escape').symlink_to(tmp_path)
    except (OSError,NotImplementedError):pass
    else:
        with pytest.raises(ValueError):batch.preview(app,{**cfg(),'folder':'escape'})
    view=batch.preview(app,cfg());src=data/(token+'.csv')
    src.write_text(src.read_text(encoding='utf-8')+'3,15\n',encoding='utf-8')
    result=wait(app,batch.start(app,view['task_id'])['task_id'])
    assert result['counts']['failed']==1
    with sqlite3.connect(app/'datasets'/'aas_telemetry_v2.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0


def test_interrupted_task_can_retry_without_reimport(tmp_path,monkeypatch):
    app,_,_,_=fixture(tmp_path,monkeypatch)
    view=batch.preview(app,cfg());tid=view['task_id']
    # Simulate service death by persisting running without live thread.
    path=batch._record_path(app,tid);record=batch._read(path);record['status']='running';batch._save(path,record)
    assert batch.status(app,tid)['status']=='interrupted'
    retry=batch.start(app,tid,retry=True)
    assert retry['task_id']!=tid
    assert wait(app,retry['task_id'])['status']=='complete'
    assert batch.status(app,tid)['status']=='interrupted'


def test_ui_contract_and_js_syntax():
    import shutil,subprocess
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/import.html').read_text(encoding='utf-8')
    js=(root/'web/visualization/telemetry-v2-batch.js').read_text(encoding='utf-8')
    for part in ('v2BatchPanel','v2BatchFolder','v2BatchTargets','v2BatchPreview','v2BatchStart',
                 'v2BatchHistory','v2BatchNormal'):
        assert 'id="'+part+'"' in html and "$('"+part+"')" in js or part=='v2BatchPanel' and 'v2BatchPanel' in html
    assert '/api/v2/telemetry/batch/' in js
    if node:=shutil.which('node'):
        result=subprocess.run([node,'--check',str(root/'web/visualization/telemetry-v2-batch.js')],
                              capture_output=True,text=True)
        assert result.returncode==0,result.stderr


def test_batch_http_contract_and_authenticated_boundary(tmp_path,monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request,urlopen
    from scripts import visualization_server as app_runtime
    from scripts.server.application_context import context_from_runtime,bind_http_context
    from scripts.server.handlers import DashboardHandler
    from tests.http_test_handler import AuthorizedTestHandler

    app,_,_,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(app_runtime,'ROOT',app)
    monkeypatch.setattr(app_runtime,'MODELS_DIR',app/'models')
    monkeypatch.setattr(app_runtime,'MODEL_DB_PATH',app/'models'/'model.db')
    context=context_from_runtime(app_runtime)

    def invoke(server,path,payload=None):
        data=json.dumps(payload).encode() if payload is not None else None
        req=Request(f'http://127.0.0.1:{server.server_port}{path}',data=data,
                    headers={'Content-Type':'application/json'} if data else {},
                    method='POST' if data else 'GET')
        try:reply=urlopen(req,timeout=6)
        except HTTPError as exc:reply=exc
        with reply:return reply.status,json.loads(reply.read())

    def serve(handler):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        bind_http_context(server,context)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        return server,thread

    server,thread=serve(AuthorizedTestHandler)
    try:
        code,work=invoke(server,'/api/v2/telemetry/batch/workspace')
        assert code==200 and 'runs' in work['folders']
        code,review=invoke(server,'/api/v2/telemetry/batch/preview',cfg())
        assert code==200 and review['ready']
        tid=review['task_id']
        assert invoke(server,'/api/v2/telemetry/batch/status?task_id='+tid)[1]['status']=='previewed'
        assert invoke(server,'/api/v2/telemetry/batch/start',{'task_id':tid})[0]==400
        assert invoke(server,'/api/v2/telemetry/batch/start',{'task_id':tid,'confirm':True})[0]==202
        task_code,task=invoke(server,'/api/task-status?task_id='+tid)
        assert task_code==200 and task['task_type']=='batch_import'
        assert wait(app,tid)['status']=='complete'
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()

    server,thread=serve(DashboardHandler)
    try:
        assert invoke(server,'/api/v2/telemetry/batch/history')[0]==401
        code,error=invoke(server,'/api/v2/telemetry/batch/preview',cfg())
        assert code==401 and error['code']=='AUTH_REQUIRED'
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def test_unauthorized_post_body_is_json_even_when_request_has_unread_bytes(tmp_path, monkeypatch):
    """Windows must receive the JSON 401 body, not a reset/truncated response."""
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from scripts import visualization_server as app_runtime
    from scripts.server.application_context import context_from_runtime, bind_http_context
    from scripts.server.handlers import DashboardHandler

    app, _, _, _ = fixture(tmp_path, monkeypatch)
    server = ThreadingHTTPServer(('127.0.0.1', 0), DashboardHandler)
    monkeypatch.setattr(app_runtime,'ROOT',app)
    monkeypatch.setattr(app_runtime,'MODELS_DIR',app/'models')
    monkeypatch.setattr(app_runtime,'MODEL_DB_PATH',app/'models'/'model.db')
    bind_http_context(server, context_from_runtime(app_runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({'untrusted_payload': 'x' * 8192}).encode('utf-8')
        request = Request(f'http://127.0.0.1:{server.server_port}/api/v2/telemetry/batch/preview',
                          data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            reply = urlopen(request, timeout=6)
        except HTTPError as exc:
            reply = exc
        with reply:
            assert reply.status == 401
            raw = reply.read()
            assert reply.headers.get('Content-Type', '').startswith('application/json')
            assert int(reply.headers['Content-Length']) == len(raw) > 0
            assert json.loads(raw)['code'] == 'AUTH_REQUIRED'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

def test_http_responses_survive_invalid_windows_original_stdout(tmp_path, monkeypatch):
    """A WinError 6 in original stdout must not turn valid HTTP replies into 500s."""
    import sys
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from scripts import visualization_server as app_runtime
    from scripts.server.application_context import context_from_runtime, bind_http_context
    from scripts.server.handlers import DashboardHandler
    from tests.http_test_handler import AuthorizedTestHandler

    class InvalidWindowsHandle:
        def write(self, _data):
            raise OSError(6, '[WinError 6] invalid handle')
        def flush(self):
            raise OSError(6, '[WinError 6] invalid handle')

    app, _, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app_runtime,'ROOT',app)
    monkeypatch.setattr(app_runtime,'MODELS_DIR',app/'models')
    monkeypatch.setattr(app_runtime,'MODEL_DB_PATH',app/'models'/'model.db')
    context = context_from_runtime(app_runtime)

    def request(handler, path, *, data=None):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        bind_http_context(server, context)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = Request(f'http://127.0.0.1:{server.server_port}{path}',
                          data=data, headers={'Content-Type': 'application/json'} if data else {},
                          method='POST' if data else 'GET')
            try:
                response = urlopen(req, timeout=6)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.loads(response.read())
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    with monkeypatch.context() as patch:
        patch.setattr(sys, '__stdout__', InvalidWindowsHandle())
        status, body = request(AuthorizedTestHandler, '/api/v2/telemetry/batch/workspace')
        assert status == 200 and 'folders' in body
        status, body = request(DashboardHandler, '/api/v2/telemetry/batch/preview',
                               data=json.dumps(cfg()).encode('utf-8'))
        assert status == 401 and body['code'] == 'AUTH_REQUIRED'


def test_http_logging_never_raises_on_unusable_error_stream(monkeypatch):
    """Request logging cannot break responses even when stderr is invalid."""
    import sys
    from scripts.server.handlers import DashboardHandler

    class InvalidStream:
        def write(self, _data):
            raise OSError(6, '[WinError 6] invalid handle')
        def flush(self):
            raise ValueError('closed stream')

    class RequestLike:
        client_address = ('127.0.0.1', 1234)

    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stderr', InvalidStream())
        DashboardHandler.log_message(RequestLike(), '"%s" %d', 'GET /api/v2/status', 200)
        patch.setattr(sys, 'stderr', None)
        DashboardHandler.log_message(RequestLike(), '"%s" %d', 'GET /api/v2/status', 200)
