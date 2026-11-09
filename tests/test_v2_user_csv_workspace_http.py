from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
from types import SimpleNamespace

from scripts.server.handler_get import GetRoutesMixin
from scripts.server.handler_post import PostRoutesMixin
from scripts.server import v2_user_csv_workspace as ws
from tests.test_v2_user_csv_workspace_real_import import _meta


class _Main:
    def __init__(self, root):
        self.ROOT = root
        self.responses = []
        self.task_status = {}
    def json_response(self, _handler, data, status=200):
        self.responses.append((status, data))
        return self.responses[-1]
    def get_public_task_status(self, task_id):
        return self.task_status.get(task_id)


class _Post(PostRoutesMixin):
    def __init__(self, main, path, raw=b'', headers=None):
        self.path = path
        self.server = SimpleNamespace(aas_application=main)
        self.rfile = io.BytesIO(raw)
        self.headers = {'Content-Length': str(len(raw)), **(headers or {})}
    def _handle_auth_post(self, *_args):
        return False
    def _require_auth(self, *_args, **_kwargs):
        return True


class _Get(GetRoutesMixin):
    def __init__(self, main, path):
        self.path = path
        self.server = SimpleNamespace(aas_application=main)
    def _require_auth(self, *_args, **_kwargs):
        return True


def _dispatch_upload(main, raw, metadata, role):
    encoded = base64.b64encode(json.dumps(metadata, ensure_ascii=False).encode()).decode()
    handler = _Post(main, '/api/v2/user-csv-workspace/import-source', raw, {
        'X-AAS-Import-Metadata': encoded, 'X-AAS-CSV-Role': role,
        'Content-Type': 'application/octet-stream'})
    handler._dispatch_POST()
    return main.responses[-1]


def test_authenticated_http_inspect_preview_commit_list_and_catalog_isolation(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'historical-db-sentinel')
    history_hash = hashlib.sha256(history.read_bytes()).hexdigest()
    main = _Main(tmp_path)
    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(f'{i},{i+1},0\n' for i in range(20))).encode()

    status, inspected = _dispatch_upload(main, raw,
        {'operation': 'inspect', 'filename': 'http.csv'}, 'train')
    assert status == 200 and inspected['format'] == 'csv'

    metadata = _meta(filename='http.csv', namespace='USER_CSV_HTTP')
    preview_status, preview = _dispatch_upload(main, raw, {**metadata, 'operation': 'preview'}, 'train')
    assert preview_status == 200 and preview['status'] == 'preview'
    with sqlite3.connect(ws.database_path(tmp_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0] == 0

    commit_status, committed = _dispatch_upload(main, raw, metadata, 'train')
    assert commit_status == 200 and committed['status'] == 'imported'
    assert committed['storage_scope'] == 'user_csv_workspace'
    assert committed['historical_database_touched'] is False
    assert hashlib.sha256(history.read_bytes()).hexdigest() == history_hash

    main.responses.clear()
    _Get(main, '/api/v2/user-csv-workspace/list?role=train')._dispatch_GET()
    status, listing = main.responses[-1]
    assert status == 200 and len(listing['datasets']) == 1
    dataset = listing['datasets'][0]
    assert dataset['id'] == committed['dataset_id']
    assert dataset['import_status'] == 'imported'

    main.responses.clear()
    _Get(main, '/api/v2/run-dataset/catalog?source=user_csv&dataset_id=' + dataset['id'])._dispatch_GET()
    status, catalog = main.responses[-1]
    assert status == 200
    assert [run['source_id'] for run in catalog['runs']] == [committed['source_id']]
    assert catalog['runs'][0]['allowed_role'] == 'train'
    assert hashlib.sha256(history.read_bytes()).hexdigest() == history_hash


def test_user_csv_delete_requires_no_live_task_reference_and_preserves_history(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'history')
    item = ws.import_csv(tmp_path, b'time_s,x\n0,1\n', 'delete.csv', 'train', {}, format_confirmed=True)
    with sqlite3.connect(ws.database_path(tmp_path)) as db:
        db.execute('INSERT INTO user_csv_task_refs VALUES(?,?,?)',
                   ('task-live', item['id'], '2026-11-08T00:00:00+00:00'))
    main = _Main(tmp_path)
    main.task_status['task-live'] = {'status': 'running'}
    body = json.dumps({'dataset_id': item['id'], 'confirm': True}).encode()
    _Post(main, '/api/v2/user-csv-workspace/delete', body)._dispatch_POST()
    assert main.responses[-1][0] == 409
    assert ws.get_dataset(tmp_path, item['id'])['id'] == item['id']

    main.task_status['task-live'] = {'status': 'completed'}
    main.responses.clear()
    _Post(main, '/api/v2/user-csv-workspace/delete', body)._dispatch_POST()
    assert main.responses[-1][0] == 200
    assert main.responses[-1][1]['deleted'] is True
    assert history.read_bytes() == b'history'


def test_async_http_commit_persists_dataset_id_in_task_result(tmp_path):
    from scripts.server.task_runtime import TaskRuntime

    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'history-sentinel')
    runtime = TaskRuntime.for_root(tmp_path)
    main = _Main(tmp_path)
    main.create_task = runtime.create_task
    main.bind_task_thread = runtime.bind_task_thread
    main.update_task_progress = runtime.update_task_progress
    main.is_cancel_requested = runtime.is_cancel_requested
    main.set_task_cancellable = runtime.set_task_cancellable
    main.complete_task = runtime.complete_task
    main.get_public_task_status = runtime.get_public_task_status

    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(f'{i},{i+1},0\n' for i in range(20))).encode()
    metadata = _meta(filename='async.csv', namespace='USER_CSV_HTTP_ASYNC')
    status, accepted = _dispatch_upload(main, raw, metadata, 'train')
    assert status == 202 and accepted['status'] == 'accepted'
    deadline = __import__('time').monotonic() + 20
    task = None
    while __import__('time').monotonic() < deadline:
        task = runtime.get_public_task_status(accepted['task_id'])
        if task and task['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
            break
        __import__('time').sleep(0.05)
    assert task and task['status'] == 'completed', task
    assert task['result']['dataset_id']
    assert task['result']['storage_scope'] == 'user_csv_workspace'
    assert task['result']['historical_database_touched'] is False
    assert ws.get_dataset(tmp_path, task['result']['dataset_id'])['import_status'] == 'imported'
    assert history.read_bytes() == b'history-sentinel'
