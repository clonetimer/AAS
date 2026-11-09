"""Live HTTP contracts for the safe task status and history APIs."""
from __future__ import annotations

import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts import visualization_server as app
from scripts.server.task_runtime import TaskRuntime
from tests.http_test_handler import AuthorizedTestHandler


def test_task_history_and_single_status_are_safe_and_bounded(tmp_path, monkeypatch):
    runtime = TaskRuntime.for_root(tmp_path)
    failed_id = uuid.uuid4().hex
    interrupted_id = uuid.uuid4().hex
    archive_id = uuid.uuid4().hex
    runtime.create_training_task(failed_id, {'password': 'PRIVATE_SECRET',
                                             'dataset_path': '/private/telemetry.csv'})
    runtime.update_task_progress(failed_id, 35, '算法执行中', stage='algorithm_execution')
    runtime.complete_task(failed_id, None, 'internal /private/telemetry.csv traceback')
    runtime.create_training_task(interrupted_id, {'algorithm': 'zscore'})
    runtime.update_task_progress(interrupted_id, 60, '正在训练', stage='algorithm_execution')
    runtime.create_training_task(archive_id, {'algorithm': 'zscore'})
    runtime.complete_task(archive_id, {'status': 'partial', 'archive_status': 'failed',
        'archive_error': 'internal disk path /private/outputs',
        'model_dir': '/private/models/model.pkl',
        'summary': {'config': {'private': 'ARCHIVE_PRIVATE'},
                    'result': {'rows': [{'timestamp': 1, 'value': 999}]}}})
    from scripts.server.task_diagnostics import archive_attention, diagnose_failure
    sync_eval_id = runtime.record_terminal_task('evaluation', 'failed', 'evaluation_execution',
        '算法执行超时', failure=diagnose_failure(RuntimeError('private worker path'),
                                                   'evaluation_execution', 'evaluation'))
    sync_score_id = runtime.record_terminal_task('score_only', 'completed', 'report_archive',
        '计算完成但报告待处理', attention=archive_attention(),
        result={'archive_status': 'failed', 'input_path': '/private/score.csv'})
    # A fresh runtime simulates process restart and turns the in-flight record into interrupted.
    runtime = TaskRuntime.for_root(tmp_path)

    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app, 'TRAINING_TASKS', runtime.tasks)
    monkeypatch.setattr(app, 'TASKS_LOCK', runtime.lock)
    monkeypatch.setattr(app, 'TASK_RESOURCE_TRACKER', None)
    monkeypatch.setattr(app, 'TASK_RUNTIME', runtime)

    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + f'/api/train-status?task_id={failed_id}', timeout=8) as response:
            status = json.load(response)
        assert status['status'] == 'failed'
        assert status['failure']['code'] == 'algorithm_failed'
        assert status['stage'] == 'algorithm_execution'
        assert 'config' not in status
        assert 'PRIVATE_SECRET' not in json.dumps(status)
        assert '/private/telemetry.csv' not in json.dumps(status)
        assert 'traceback' not in json.dumps(status)

        with urlopen(base + '/api/task-history?limit=2', timeout=8) as response:
            history = json.load(response)
        assert history['total'] == 5
        assert len(history['items']) == 2
        assert {item['task_id'] for item in history['items']} <= {failed_id, interrupted_id, archive_id, sync_eval_id, sync_score_id}
        with urlopen(base + '/api/task-history?limit=5', timeout=8) as response:
            complete_history = json.load(response)
        archive_item = next(item for item in complete_history['items']
                            if item.get('task_type') == 'score_only')
        assert archive_item['task_id'] == sync_score_id
        eval_item = next(item for item in complete_history['items']
                         if item.get('task_type') == 'evaluation')
        assert eval_item['task_id'] == sync_eval_id
        assert eval_item['failure']['code'] == 'algorithm_worker_failed'
        assert 'summary' not in archive_item['result']
        assert 'model_dir' not in archive_item['result']
        assert 'ARCHIVE_PRIVATE' not in json.dumps(complete_history, ensure_ascii=False)
        assert '/private/outputs' not in json.dumps(complete_history, ensure_ascii=False)

        try:
            urlopen(base + '/api/train-status?task_id=missing', timeout=8)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('unknown task id must return 404')
    finally:
        server.shutdown()
        thread.join(timeout=8)
        server.server_close()


def test_bound_http_context_uses_the_same_scoped_task_history(tmp_path):
    from scripts.server.application_context import bind_http_context
    from tests.test_phase38_runtime_services import _scoped_context

    context = _scoped_context(tmp_path / 'bound')
    task_id = uuid.uuid4().hex
    context.create_training_task(task_id, {'algorithm': 'zscore', 'private': 'do-not-return'})
    context.update_task_progress(task_id, 22, '正在读取数据', stage='data_loading')
    from scripts.server.task_diagnostics import diagnose_failure
    context.complete_task(task_id, None, 'input missing',
                          failure=diagnose_failure(FileNotFoundError('missing'), 'data_loading'))

    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    bind_http_context(server, context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/task-history', timeout=8) as response:
            history = json.load(response)
        assert history['total'] == 1
        assert history['items'][0]['task_id'] == task_id
        assert history['items'][0]['failure']['code'] == 'input_missing'
        assert 'config' not in history['items'][0]
    finally:
        server.shutdown()
        thread.join(timeout=8)
        server.server_close()
