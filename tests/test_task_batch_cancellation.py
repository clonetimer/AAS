from __future__ import annotations

import hashlib
import os
import time

from scripts.server import telemetry_v2_batch_tasks as batch
from scripts.server.task_runtime import TaskRuntime


def _batch_fixture(tmp_path, monkeypatch, status='running'):
    root = tmp_path / 'managed'
    root.mkdir()
    app_root = tmp_path / 'app'
    task_id = 'a' * 32
    files = []
    snapshots = []
    for index in range(2):
        name = f'run-{index}.csv'
        path = root / name
        path.write_text('timestamp,x\n1,2\n', encoding='utf-8')
        stat = path.stat()
        files.append({'path': name, 'config': {'role': 'train'}})
        snapshots.append({'relative_path': name, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    targets = root / 'targets.zip'
    targets.write_bytes(b'fake-targets-archive')
    digest = hashlib.sha256(targets.read_bytes()).hexdigest()
    monkeypatch.setattr(batch, '_root', lambda: root)
    plan = {
        'files': files,
        'sources': snapshots,
        'folder': '.',
        'targets_archive': 'targets.zip',
        'targets_sha256': digest,
        'database': str(tmp_path / 'datasets' / 'telemetry.db'),
    }
    record = {
        'id': task_id, 'status': status, 'created_utc': batch._utc(),
        'updated_utc': batch._utc(), 'files_total': 2, 'files_processed': 0,
        'counts': {'imported': 0, 'skipped': 0, 'failed': 0}, 'results': [],
        'preview': {},
    }
    batch._save(batch._plan_path(app_root, task_id), plan)
    batch._save(batch._record_path(app_root, task_id), record)
    return root, app_root, task_id


def test_batch_cancel_stops_at_file_transaction_boundary(tmp_path, monkeypatch):
    root, app_root, task_id = _batch_fixture(tmp_path, monkeypatch)
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(app_root)
    runtime.create_task(task_id, 'batch_import', {})
    imported = []

    def fake_import_file(database, source, config):
        imported.append(source.name)
        # Simulate a cancel arriving while the first file transaction is in progress.
        runtime.request_cancel_task(task_id)
        return {'status': 'imported', 'filename': source.name, 'source_id': 1}

    monkeypatch.setattr(batch, 'import_file', fake_import_file)

    batch._worker(app_root, task_id, task_runtime=runtime)

    record = batch._read(batch._record_path(app_root, task_id))
    public = runtime.get_public_task_status(task_id)
    assert imported == ['run-0.csv']
    assert record['status'] == 'cancelled'
    assert record['files_processed'] == 1
    assert record['counts']['imported'] == 1
    assert record['results'][0]['status'] == 'imported'
    assert public['status'] == 'cancelled'
    assert public['progress'] < 100


def test_batch_cancel_before_first_file_keeps_all_receipts_empty(tmp_path, monkeypatch):
    root, app_root, task_id = _batch_fixture(tmp_path, monkeypatch)
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(app_root)
    runtime.create_task(task_id, 'batch_import', {})
    runtime.request_cancel_task(task_id)
    imported = []
    monkeypatch.setattr(batch, 'import_file', lambda *_args: imported.append(True))

    batch._worker(app_root, task_id, task_runtime=runtime)

    record = batch._read(batch._record_path(app_root, task_id))
    assert imported == []
    assert record['status'] == 'cancelled'
    assert record['files_processed'] == 0
    assert record['results'] == []
    assert runtime.get_public_task_status(task_id)['status'] == 'cancelled'


def test_batch_start_creates_same_id_in_unified_task_runtime(tmp_path, monkeypatch):
    root, app_root, task_id = _batch_fixture(tmp_path, monkeypatch, status='previewed')
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(app_root)
    monkeypatch.setattr(batch, 'active_path', lambda _app_root: tmp_path / 'datasets' / 'telemetry.db')
    database = tmp_path / 'datasets' / 'telemetry.db'
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=True, name=None):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(batch.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(batch, 'import_file', lambda *_args: {'status': 'imported', 'source_id': 2})

    response = batch.start(app_root, task_id, task_runtime=runtime)

    assert response['task_id'] == task_id
    public = runtime.get_public_task_status(task_id)
    assert public['task_type'] == 'batch_import'
    assert public['status'] == 'completed'
    assert public['result']['files_processed'] == 2
