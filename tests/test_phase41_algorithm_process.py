"""Phase 41: real subprocess fit/score and subprocess failure containment."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from scripts.server.algorithm_process import (
    AlgorithmProcessService, AlgorithmTimeoutError, AlgorithmWorkerError,
)
from scripts.server.v2_run_dataset import validate_training_selection
from tests.test_v2_run_subset_phase8 import labeled, choose
from scripts.core.model_manager import load_model_config, validate_saved_model


def supervisor(tmp_path):
    return AlgorithmProcessService(tmp_path, tmp_path / 'models')


def basic_job_kwargs():
    return dict(algorithm='three_sigma', algorithm_params={},
                selection=choose([1, 2]), train_ratio=.75,
                normalization_method='zscore', training_strategy='standard',
                expected={}, database='aas_telemetry_v2.sqlite')


def test_real_subprocess_training_scoring_and_separate_test(tmp_path, monkeypatch):
    data_dir = tmp_path / 'datasets'
    data_dir.mkdir()
    db = labeled.__wrapped__(data_dir)
    monkeypatch.setenv('AAS_V2_DB_FILENAME', db.name)
    service = supervisor(tmp_path)
    args = basic_job_kwargs()
    preview = validate_training_selection(db, {
        'dataset_selection': args['selection'], 'train_ratio': .75})
    args['expected'] = {'feature_keys': preview['feature_keys'],
                        'train_run_evidence': preview['runs']}
    progress = []
    result = service.run_training(**args, progress=lambda p,m: progress.append((p,m)))
    assert result['status'] == 'success'
    assert progress
    model = result['model_info']['model_dir']
    assert Path(model).is_dir()
    validate_saved_model(Path(model), 'three_sigma', experiment_id=result['experiment_id'])
    dc = load_model_config(model)['config']['data_contract']
    test_selection = choose([3, 4])
    expected = {'feature_keys': args['selection']['feature_keys'],
                'train_source_ids': [1, 2],
                'train_groups': [r['group_id'] for r in dc['v2_run_manifest']['runs']]}
    output = service.run_evaluation(
        algorithm='three_sigma', selection=test_selection, expected=expected,
        scaling=dc['model_input_scaling'], feature_columns=dc['feature_columns'],
        model_dir=Path(model), database=db.name)
    assert output['status'] == 'success'
    assert output['result']['metrics']
    # Evaluation does not fit or create a second model artifact.
    assert len(list(service.models_dir.glob('model_*'))) == 1
    assert list(service.job_dir.iterdir()) == []


def test_worker_crash_is_contained_and_next_job_can_run(tmp_path, monkeypatch):
    service = supervisor(tmp_path)
    real_popen = subprocess.Popen
    # True subprocess exits hard without writing a response. No HTTP crash.
    monkeypatch.setattr('scripts.server.algorithm_process._spawn_worker',
                        lambda command, options: real_popen([sys.executable, '-c', 'import os;os._exit(17)'], **options))
    with pytest.raises(AlgorithmWorkerError, match='exit=17'):
        service.run_training(**basic_job_kwargs())
    assert list(service.job_dir.iterdir()) == []
    assert service.semaphore.acquire(blocking=False)
    service.semaphore.release()


def test_worker_timeout_terminates_real_process(tmp_path, monkeypatch):
    service = supervisor(tmp_path)
    real_popen = subprocess.Popen
    monkeypatch.setattr('scripts.server.algorithm_process._spawn_worker',
                        lambda command, options: real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **options))
    monkeypatch.setenv('AAS_ALGORITHM_TIMEOUT_SECONDS', '1')
    with pytest.raises(AlgorithmTimeoutError, match='已终止'):
        service.run_training(**basic_job_kwargs())
    assert list(service.job_dir.iterdir()) == []


def test_rejects_concurrent_overload_before_spawning(tmp_path):
    service = AlgorithmProcessService(tmp_path, tmp_path / 'models', max_parallel=1)
    assert service.semaphore.acquire(blocking=False)
    try:
        with pytest.raises(AlgorithmWorkerError, match='并发'):
            service.run_training(**basic_job_kwargs())
    finally:
        service.semaphore.release()


def test_requires_scoped_models_and_reviewed_actions(tmp_path):
    with pytest.raises(ValueError, match='inside application'):
        AlgorithmProcessService(tmp_path, tmp_path.parent / 'outside')
    service = supervisor(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        service.run_evaluation(algorithm='zscore', selection={}, expected={}, scaling={},
                               feature_columns=['x'], model_dir=tmp_path.parent / 'outside',
                               database='aas_telemetry_v2.sqlite')
    with pytest.raises(ValueError, match='reviewed'):
        service._run('user_command', {})


def test_invalid_policy_fails_closed(tmp_path, monkeypatch):
    service = supervisor(tmp_path)
    monkeypatch.setenv('AAS_ALGORITHM_TIMEOUT_SECONDS', '0')
    with pytest.raises(ValueError, match='outside supported limits'):
        service.run_training(**basic_job_kwargs())
    assert not service.job_dir.exists()


def test_context_binds_isolation_service(tmp_path):
    from scripts import visualization_server as app
    from scripts.server.application_context import context_from_runtime
    class Runtime:
        def __getattr__(self, name): return getattr(app, name)
    custom = Runtime()
    custom.ROOT=tmp_path
    custom.MODELS_DIR=tmp_path/'models'
    custom.MODEL_DB_PATH=tmp_path/'models'/'model.db'
    custom.STATIC_DIR=tmp_path/'web'
    custom.SIMULATION_DB_PATH=tmp_path/'datasets'/'sim.db'
    custom.TRAINING_TASKS={}
    custom.TASKS_LOCK=threading.Lock()
    custom.TASK_RESOURCE_TRACKER=app.TASK_RESOURCE_TRACKER
    custom.EXECUTION_REGISTRY=app.EXECUTION_REGISTRY
    context=context_from_runtime(custom)
    assert context.ALGORITHM_SUPERVISOR.root == tmp_path
    assert context.ALGORITHM_SUPERVISOR.models_dir == tmp_path/'models'


def test_http_bound_training_route_completes_using_child_process(tmp_path, monkeypatch):
    """Covers real V2 route, durable task and server-owned execution receipt."""
    import time
    from scripts import visualization_server as app
    from scripts.server.application_context import context_from_runtime
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.execution_provenance import ExecutionRegistry
    root = tmp_path
    ds = root / 'datasets'
    ds.mkdir()
    db = labeled.__wrapped__(ds)
    monkeypatch.setenv('AAS_V2_DB_FILENAME', db.name)
    class Runtime:
        def __getattr__(self, name): return getattr(app, name)
    runtime = Runtime()
    runtime.ROOT = root
    runtime.MODELS_DIR = root/'models'
    runtime.MODEL_DB_PATH = root/'models'/'model.db'
    runtime.STATIC_DIR = root/'web'
    runtime.SIMULATION_DB_PATH = root/'datasets'/'sim.db'
    runtime.TRAINING_TASKS = {}
    runtime.TASKS_LOCK = threading.Lock()
    runtime.TASK_RESOURCE_TRACKER = None
    runtime.EXECUTION_REGISTRY = ExecutionRegistry()
    context = context_from_runtime(runtime)
    req = {**basic_job_kwargs(), 'dataset_selection': choose([1, 2])}
    req.pop('selection')
    req.pop('expected')
    req.pop('database')
    result = routes._training(context, json.dumps(req).encode())
    assert result['status'] == 'accepted'
    task_id = result['task_id']
    for _ in range(100):
        state = context.get_task_status(task_id)
        if state['status'] != 'running': break
        time.sleep(.15)
    assert state['status'] == 'completed', state.get('error')
    assert state['result']['experiment_id']
    receipt = state['result']['execution_receipt']
    assert context.EXECUTION_REGISTRY.consume(receipt, 'training')['identity']['experiment_id'] == state['result']['experiment_id']
    persisted = json.loads((root/'outputs'/'task_state'/f'{task_id}.json').read_text(encoding='utf-8'))
    assert persisted['status'] == 'completed'
    assert 'algorithm_params' not in persisted


def test_worker_reports_validation_failure_without_crashing_host(tmp_path, monkeypatch):
    (tmp_path/'datasets').mkdir()
    (tmp_path/'datasets'/'aas_telemetry_v2.sqlite').touch()
    monkeypatch.setenv('AAS_V2_DB_FILENAME', 'aas_telemetry_v2.sqlite')
    service = supervisor(tmp_path)
    with pytest.raises(AlgorithmWorkerError, match='算法执行失败'):
        service.run_training(**basic_job_kwargs())
    assert not list(service.job_dir.iterdir())


def test_memory_guard_kills_process_and_cleans_workspace(tmp_path, monkeypatch):
    service = supervisor(tmp_path)
    real_popen = subprocess.Popen
    monkeypatch.setattr('scripts.server.algorithm_process._spawn_worker',
                        lambda command, options: real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **options))
    monkeypatch.setattr('scripts.server.algorithm_process._tree_rss', lambda pid: 2 * 1024 * 1024)
    monkeypatch.setenv('AAS_ALGORITHM_MEMORY_MB', '1')
    with pytest.raises(AlgorithmWorkerError, match='内存上限'):
        service.run_training(**basic_job_kwargs())
    assert not list(service.job_dir.iterdir())


def test_spawn_failure_does_not_hold_semaphore_or_leave_jobs(tmp_path, monkeypatch):
    service = supervisor(tmp_path)
    def fail(command, options):
        raise OSError('injected process launch failure')
    monkeypatch.setattr('scripts.server.algorithm_process._spawn_worker', fail)
    with pytest.raises(OSError, match='injected'):
        service.run_training(**basic_job_kwargs())
    assert list(service.job_dir.iterdir()) == []
    assert service.semaphore.acquire(blocking=False)
    service.semaphore.release()
