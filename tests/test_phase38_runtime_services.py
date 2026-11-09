"""Phase 38 contract: scoped task/model state and training progress callbacks."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from scripts import visualization_server as app
from scripts.server.application_context import bind_http_context, context_from_runtime
from scripts.server.evaluation_workflow import run_evaluation
from scripts.server.model_registry_service import ModelRegistryService
from scripts.server.task_runtime import TaskRuntime
from tests.http_test_handler import AuthorizedTestHandler


class FakeTracker:
    def __init__(self):
        self.starts = []
        self.finishes = []
    def start(self, key, *, algorithm=None):
        self.starts.append((key, algorithm))
    def finish(self, key, *, status, experiment_id):
        self.finishes.append((key, status, experiment_id))
        return {'task_id': key, 'status': status, 'experiment_id': experiment_id}


def _scoped_context(root: Path):
    """Use existing reviewed HTTP operations with per-app overrides."""
    class Runtime:
        def __getattr__(self, name):
            return getattr(app, name)
    runtime = Runtime()
    runtime.ROOT = root
    runtime.STATIC_DIR = root / 'web'
    runtime.SIMULATION_DB_PATH = root / 'datasets' / 'simulation.db'
    runtime.MODELS_DIR = root / 'models'
    runtime.MODEL_DB_PATH = root / 'models' / 'model.db'
    runtime.TRAINING_TASKS = {}
    runtime.TASKS_LOCK = threading.Lock()
    runtime.TASK_RESOURCE_TRACKER = FakeTracker()
    runtime.EXECUTION_REGISTRY = object()
    return context_from_runtime(runtime)


def test_scoped_tasks_do_not_leak_or_use_compatibility_globals(tmp_path, monkeypatch):
    left = _scoped_context(tmp_path / 'left')
    right = _scoped_context(tmp_path / 'right')
    assert callable(left.record_terminal_task)
    monkeypatch.setattr(app, 'create_training_task', lambda *a: (_ for _ in ()).throw(AssertionError('global task path')))
    left.create_training_task('task-a', {'algorithm': 'zscore', 'secret': 'internal'})
    right.create_training_task('task-b', {'algorithm': 'cusum'})
    left.bind_task_thread('task-a')
    left.update_task_progress('task-a', 65, 'in progress')
    left.complete_task('task-a', {'experiment_id': 'exp-a', 'detector': object(), '_internal': 'no'})
    assert left.get_task_status('task-a')['status'] == 'completed'
    assert left.get_task_status('task-a')['result'] == {'experiment_id': 'exp-a'}
    assert left.TASK_RESOURCE_TRACKER.starts == [('task-a', 'zscore')]
    assert left.TASK_RESOURCE_TRACKER.finishes == [('task-a', 'completed', 'exp-a')]
    assert right.get_task_status('task-a') is None
    assert right.get_task_status('task-b')['status'] == 'running'


def test_scoped_model_registries_use_distinct_databases(tmp_path, monkeypatch):
    left = _scoped_context(tmp_path / 'left')
    right = _scoped_context(tmp_path / 'right')
    left.MODEL_REGISTRY.ensure_model_db()
    right.MODEL_REGISTRY.ensure_model_db()
    monkeypatch.setattr(app, 'get_models', lambda **kw: (_ for _ in ()).throw(AssertionError('global model path')))
    with sqlite3.connect(left.MODEL_DB_PATH) as conn:
        conn.execute('''INSERT INTO models (
            experiment_id, model_name, algorithm, dataset, table_name,
            created_at, published_at, file_path, metrics_json, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            ('scoped-left', 'A', 'zscore', 'v2.db', 'v2_channels',
             '2026-10-23', '2026-10-23', 'models/model_a', '{}', '{}'))
    assert len(left.get_models()) == 1
    assert right.get_models() == []
    record = left.get_models()[0]
    assert left.update_model_status(record['id'], 'evaluated')['status'] == 'success'
    assert left.get_model(record['id'])['evaluation_count'] == 1
    assert right.get_model(record['id']) is None


def test_context_model_path_capture_survives_module_mutation(tmp_path, monkeypatch):
    left = _scoped_context(tmp_path / 'app')
    monkeypatch.setattr(app, 'MODEL_DB_PATH', tmp_path / 'elsewhere' / 'model.db')
    left.MODEL_REGISTRY.ensure_model_db()
    assert left.MODEL_DB_PATH.is_file()
    assert not app.MODEL_DB_PATH.exists()


def test_scoped_model_api_via_live_http(tmp_path):
    context = _scoped_context(tmp_path / 'http')
    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    bind_http_context(server, context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/models', timeout=8) as response:
            result = json.load(response)
        assert result == {'models': [], 'total': 0}
        assert context.MODEL_DB_PATH.is_file()
    finally:
        server.shutdown()
        thread.join(timeout=8)
        server.server_close()


def test_evaluation_progress_uses_explicit_app_callbacks(tmp_path):
    progress = []
    logs = []
    class FakePipeline:
        def __init__(self, **kwargs):
            self.callback = kwargs['progress_callback']
        def run(self, **kwargs):
            self.callback(23, 'loading')
            return SimpleNamespace(
                data_contract={}, experiment_id='fake-01', model_info={},
                input_snapshot=None, input_evidence={}, result_rows=[], threshold=None,
                algorithm_result=None, algorithm='zscore', data_mode='train_test_split',
                metrics=SimpleNamespace(to_dict=lambda: {'accuracy': 1.0}),
            )
    fake = SimpleNamespace(
        resolve_managed_database_path=lambda name: tmp_path / 'telemetry.db',
        _base_table_name=lambda name: name,
        _resolve_clean_table=lambda *a, **kw: 'clean',
        active_name=lambda: 'telemetry.db',
        TrainingPipeline=FakePipeline,
        update_task_progress=lambda *args: progress.append(args),
        console_log=logs.append,
    )
    result = run_evaluation(fake, 'telemetry.db', 'v2_channels', 'zscore', task_id='task-1')
    assert result['status'] == 'success'
    assert progress == [('task-1', 23, 'loading')]
    assert logs == ['[进度:23%] loading']


def test_reject_model_db_path_outside_model_directory(tmp_path):
    with pytest.raises(ValueError, match='managed model directory'):
        ModelRegistryService(tmp_path, model_dir=tmp_path/'models', model_db_path=tmp_path/'other.db')


def test_http_evaluation_progress_updates_scoped_task(tmp_path, monkeypatch):
    class FakePipeline:
        def __init__(self, **kwargs):
            self.callback = kwargs['progress_callback']
        def run(self, **kwargs):
            self.callback(48, 'reading')
            raise RuntimeError('stop after progress')
    monkeypatch.setattr(app, 'TrainingPipeline', FakePipeline)
    db = tmp_path / 'eval' / 'datasets' / 'telemetry.db'
    db.parent.mkdir(parents=True)
    db.touch()
    ctx = _scoped_context(tmp_path / 'eval')
    ctx.create_training_task('eval-01', {'algorithm': 'zscore'})
    with pytest.raises(RuntimeError, match='stop after progress'):
        ctx.run_evaluation('telemetry.db', 'v2_channels', 'zscore', task_id='eval-01')
    assert ctx.get_task_status('eval-01')['progress'] == 48
    assert ctx.get_task_status('eval-01')['message'] == 'reading'


def test_two_http_servers_do_not_share_training_status(tmp_path):
    left = _scoped_context(tmp_path / 'left-http')
    right = _scoped_context(tmp_path / 'right-http')
    left.create_training_task('same-id', {'algorithm': 'zscore'})
    left.update_task_progress('same-id', 71, 'left only')
    right.create_training_task('right-id', {'algorithm': 'cusum'})
    servers = []
    try:
        for context in (left, right):
            server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
            bind_http_context(server, context)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            servers.append((server, worker))
        def get(server, key):
            try:
                response = urlopen(f'http://127.0.0.1:{server.server_port}/api/train-status?task_id={key}', timeout=8)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)
        assert get(servers[0][0], 'same-id')[1]['progress'] == 71
        assert get(servers[1][0], 'same-id')[0] == 404
        assert get(servers[0][0], 'right-id')[0] == 404
        assert get(servers[1][0], 'right-id')[1]['progress'] == 0
    finally:
        for server, worker in servers:
            server.shutdown()
            worker.join(timeout=8)
            server.server_close()
