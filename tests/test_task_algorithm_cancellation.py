from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scripts.server.algorithm_process import (
    AlgorithmCancelledError,
    AlgorithmProcessService,
)
from scripts.server.task_runtime import TaskRuntime


def test_algorithm_process_service_terminates_worker_and_releases_slot_on_cancel(tmp_path, monkeypatch):
    import scripts.server.algorithm_process as process_module

    models_dir = tmp_path / 'models'
    models_dir.mkdir()
    service = AlgorithmProcessService(tmp_path, models_dir, max_parallel=1)
    spawned = {}

    def spawn_sleeping_worker(command, options):
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'], **options)
        spawned['proc'] = proc
        return proc

    monkeypatch.setattr(process_module, '_spawn_worker', spawn_sleeping_worker)
    started = time.monotonic()

    with pytest.raises(AlgorithmCancelledError, match='取消'):
        service._run(
            'training', {},
            cancel_requested=lambda: time.monotonic() - started >= 0.25,
        )

    proc = spawned['proc']
    assert proc.poll() is not None
    assert service.semaphore.acquire(blocking=False) is True
    service.semaphore.release()


def test_training_supervisor_forwards_cancel_callback(tmp_path, monkeypatch):
    models_dir = tmp_path / 'models'
    models_dir.mkdir()
    service = AlgorithmProcessService(tmp_path, models_dir)
    callback = lambda: True
    observed = {}

    def fake_run(action, payload, progress=None, cancel_requested=None):
        observed['action'] = action
        observed['cancel_requested'] = cancel_requested
        return {'status': 'ok'}

    monkeypatch.setattr(service, '_run', fake_run)
    result = service.run_training(
        algorithm='zscore', algorithm_params={}, selection={}, train_ratio=0.8,
        normalization_method='min_max', training_strategy='standard', expected={},
        database='telemetry.db', cancel_requested=callback,
    )

    assert result == {'status': 'ok'}
    assert observed == {'action': 'training', 'cancel_requested': callback}


def test_training_route_marks_accepted_cancel_as_cancelled_and_skips_provenance(
        tmp_path, monkeypatch):
    import scripts.server.telemetry_v2_routes as routes
    import scripts.server.training_reliability as reliability

    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(tmp_path)
    cleaned = []
    issued = []

    class ImmediateThread:
        def __init__(self, target, daemon=True):
            self.target = target
        def start(self):
            self.target()

    class CancellingSupervisor:
        def run_training(self, **kwargs):
            task_id = next(iter(runtime.tasks))
            assert kwargs['cancel_requested']() is False
            runtime.request_cancel_task(task_id)
            raise AlgorithmCancelledError('用户取消训练')

    main = SimpleNamespace(
        ROOT=tmp_path,
        ALGORITHM_SUPERVISOR=CancellingSupervisor(),
        create_training_task=runtime.create_training_task,
        bind_task_thread=runtime.bind_task_thread,
        update_task_progress=runtime.update_task_progress,
        is_cancel_requested=runtime.is_cancel_requested,
        set_task_cancellable=runtime.set_task_cancellable,
        complete_task=runtime.complete_task,
        EXECUTION_REGISTRY=SimpleNamespace(issue=lambda **kwargs: issued.append(kwargs)),
    )
    preview = {
        'feature_keys': ['x'], 'runs': [{'run_id': 'r1'}], 'aligned_count': 10,
        'normal_count': 8, 'fault_count': 2, 'sample_check': {'ok': True},
    }
    monkeypatch.setattr(routes, '_db', lambda _main: tmp_path / 'telemetry.db')
    monkeypatch.setattr(routes, 'validate_training_selection', lambda *_args: preview)
    monkeypatch.setattr(routes.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(reliability, 'cleanup_unpublished_artifact', lambda _main, summary: cleaned.append(summary))

    response = routes._training(main, b'{"algorithm":"zscore","dataset_selection":{"method":"exact"}}')

    task_id = response['task_id']
    status = runtime.get_public_task_status(task_id)
    assert response['status'] == 'accepted'
    assert status['status'] == 'cancelled', status
    assert status['result'] is None
    assert cleaned == [None]
    assert issued == []
