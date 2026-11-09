from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.server.algorithm_process import AlgorithmCancelledError, AlgorithmProcessService, AlgorithmTimeoutError
from scripts.server.task_diagnostics import TaskOperationError
from scripts.server.task_runtime import TaskRuntime


def _main(tmp_path):
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(tmp_path)
    main = SimpleNamespace(
        create_task=runtime.create_task,
        bind_task_thread=runtime.bind_task_thread,
        update_task_progress=runtime.update_task_progress,
        is_cancel_requested=runtime.is_cancel_requested,
        set_task_cancellable=runtime.set_task_cancellable,
        complete_task=runtime.complete_task,
        json_response=lambda _handler, payload, status=200: {'http_status': status, **payload},
    )
    return runtime, main


class ImmediateThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


@pytest.mark.parametrize(
    ('path', 'operation', 'task_type'),
    [
        ('/api/v2/evaluation/run', '_evaluation', 'evaluation'),
        ('/api/v2/inference/run', '_inference', 'score_only'),
    ],
)
def test_evaluation_and_score_only_routes_return_durable_async_task(
        tmp_path, monkeypatch, path, operation, task_type):
    import scripts.server.telemetry_v2_routes as routes

    runtime, main = _main(tmp_path)
    monkeypatch.setattr(routes.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(routes, operation, lambda _main, _body, task_id=None: {
        'status': 'success', 'archive_status': 'complete', 'experiment_id': f'{task_type}-exp',
    })

    response = routes.handle_post(object(), SimpleNamespace(path=path), main, b'{}')

    assert response['http_status'] == 202
    assert response['status'] == 'accepted'
    assert isinstance(response['task_id'], str)
    public = runtime.get_public_task_status(response['task_id'])
    assert public['task_type'] == task_type
    assert public['status'] == 'completed'
    assert public['result']['experiment_id'] == f'{task_type}-exp'
    journal = (tmp_path / 'outputs' / 'task_state' / f"{response['task_id']}.json").read_text('utf-8')
    assert 'config' not in journal


def test_async_evaluation_failure_is_a_durable_safe_task(tmp_path, monkeypatch):
    import scripts.server.telemetry_v2_routes as routes

    runtime, main = _main(tmp_path)
    monkeypatch.setattr(routes.threading, 'Thread', ImmediateThread)

    def fail(_main, _body, task_id=None):
        raise TaskOperationError(
            'evaluation_execution', AlgorithmTimeoutError('private worker path /secret/job'), 'evaluation')

    monkeypatch.setattr(routes, '_evaluation', fail)
    response = routes.handle_post(
        object(), SimpleNamespace(path='/api/v2/evaluation/run'), main, b'{}')

    assert response['http_status'] == 202
    public = runtime.get_public_task_status(response['task_id'])
    assert public['status'] == 'failed'
    assert public['failure']['code'] == 'algorithm_timeout'
    assert public['failure']['stage'] == 'evaluation_execution'
    assert '/secret/job' not in str(public)


def test_async_score_only_cancel_becomes_cancelled_without_failure(tmp_path, monkeypatch):
    import scripts.server.telemetry_v2_routes as routes

    runtime, main = _main(tmp_path)
    monkeypatch.setattr(routes.threading, 'Thread', ImmediateThread)

    def cancel(_main, _body, task_id=None):
        runtime.request_cancel_task(task_id)
        raise AlgorithmCancelledError('user cancelled /private/job')

    monkeypatch.setattr(routes, '_inference', cancel)
    response = routes.handle_post(
        object(), SimpleNamespace(path='/api/v2/inference/run'), main, b'{}')

    public = runtime.get_public_task_status(response['task_id'])
    assert public['status'] == 'cancelled'
    assert public['failure'] is None
    assert public['result'] is None
    assert '/private/job' not in str(public)


def test_async_operation_thread_start_failure_is_durable(tmp_path, monkeypatch):
    import scripts.server.telemetry_v2_routes as routes

    runtime, main = _main(tmp_path)

    class BrokenThread:
        def __init__(self, target, daemon=True):
            self.target = target
        def start(self):
            raise RuntimeError('thread start failed /private/path')

    monkeypatch.setattr(routes.threading, 'Thread', BrokenThread)
    monkeypatch.setattr(routes, '_evaluation', lambda *_args, **_kwargs: {'status': 'success'})

    response = routes.handle_post(
        object(), SimpleNamespace(path='/api/v2/evaluation/run'), main, b'{}')

    assert response['http_status'] == 202
    public = runtime.get_public_task_status(response['task_id'])
    assert public['status'] == 'failed'
    assert public['failure']['stage'] == 'worker_start'
    assert '/private/path' not in str(public)


def test_evaluation_and_score_only_supervisors_forward_progress_and_cancel(tmp_path, monkeypatch):
    models_dir = tmp_path / 'models'
    model_dir = models_dir / 'm1'
    model_dir.mkdir(parents=True)
    service = AlgorithmProcessService(tmp_path, models_dir)
    callback = lambda: True
    progress = lambda *_args: None
    observed = []

    def fake_run(action, payload, progress=None, cancel_requested=None):
        observed.append((action, progress, cancel_requested))
        return {'status': action}

    monkeypatch.setattr(service, '_run', fake_run)
    service.run_evaluation(
        algorithm='zscore', selection={}, expected={}, scaling={}, feature_columns=[],
        model_dir=model_dir, database='telemetry.db', progress=progress, cancel_requested=callback)
    service.run_score_only(
        algorithm='zscore', selection={}, expected={}, scaling={}, feature_columns=[],
        model_dir=model_dir, database='telemetry.db', progress=progress, cancel_requested=callback)

    assert observed == [
        ('evaluation', progress, callback),
        ('score_only', progress, callback),
    ]


def test_evaluation_cancel_wins_before_provenance_and_report_archive(tmp_path, monkeypatch):
    import scripts.server.telemetry_v2_routes as routes

    runtime, main = _main(tmp_path)
    task_id = 'evaluation-cancel-before-receipt'
    runtime.create_task(task_id, 'evaluation', {})
    model_dir = tmp_path / 'models' / 'm1'
    model_dir.mkdir(parents=True)
    db_path = tmp_path / 'datasets' / 'telemetry.db'
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    updated = []
    issued = []
    archived = []
    schema = {'version': 1, 'semantics': {'x': 'physical measurement'}}
    data_contract = {
        'version': 7,
        'v2_run_selection': {
            'method': 'exact', 'tolerance_seconds': 0,
            'feature_keys': ['x'], 'source_ids': ['source-train'],
        },
        'v2_run_manifest': {'runs': [{'group_id': 'train-group'}]},
        'model_input_scaling': {'enabled': True},
        'feature_columns': ['x'],
    }

    class CancellingSupervisor:
        def run_evaluation(self, **kwargs):
            assert kwargs['cancel_requested']() is False
            runtime.request_cancel_task(task_id)
            return {'result': {'metrics': {'f1': 0.9}, 'input_evidence': {'source': 'test'}},
                    '_input_snapshot': {'digest': 'snapshot'}}

    main.ROOT = tmp_path
    main.get_model = lambda model_id: {
        'file_path': str(model_dir), 'algorithm': 'zscore', 'model_name': 'model-1'}
    main.resolve_managed_model_dir = lambda _path: model_dir
    main.load_model_config = lambda _path: {'config': {'data_contract': data_contract}}
    main.update_model_evaluation = lambda model_id: updated.append(model_id)
    main.EXECUTION_REGISTRY = SimpleNamespace(issue=lambda **kwargs: issued.append(kwargs))
    main.ALGORITHM_SUPERVISOR = CancellingSupervisor()
    monkeypatch.setattr(routes, '_db', lambda _main: db_path)
    monkeypatch.setattr(routes, 'active_name', lambda: 'telemetry.db')
    monkeypatch.setattr(routes, 'verify_stored_feature_schema', lambda _dc: schema)
    monkeypatch.setattr(routes, 'load_selection', lambda *_args, **_kwargs: ([], {
        'runs': [{'run_id': 'test-run'}], 'time_window': [1, 10], 'method': 'exact',
        'sample_check': {'ok': True}, 'label_target': {'name': 'fault'},
        'label_coverage': {'covered': 10},
    }))
    monkeypatch.setattr(routes, '_auto_archive', lambda *_args: archived.append(True))

    with pytest.raises(AlgorithmCancelledError):
        routes._evaluation(main, b'{"model_id":1,"dataset_selection":{"method":"exact"}}',
                           task_id=task_id)

    assert runtime.get_public_task_status(task_id)['status'] == 'cancelling'
    assert updated == []
    assert issued == []
    assert archived == []
