"""Structured, user-safe diagnostics for failed V2 tasks."""
from __future__ import annotations

import errno
import json
import sqlite3
import uuid
from pathlib import Path

from scripts.server.algorithm_process import AlgorithmTimeoutError
from scripts.server.task_diagnostics import diagnose_failure
from scripts.server.task_runtime import TaskRuntime


def test_disk_full_has_stage_and_actionable_recovery_without_raw_os_error():
    failure = diagnose_failure(OSError(errno.ENOSPC, 'No space left on device', '/private/aas/outputs'),
                               'report_archive')
    assert failure['code'] == 'disk_full'
    assert failure['stage'] == 'report_archive'
    assert failure['stage_label'] == '实验报告归档'
    assert failure['title'] == '磁盘空间不足'
    assert '释放' in failure['recovery_action']
    assert failure['retryable'] is True
    assert '/private/aas' not in json.dumps(failure, ensure_ascii=False)
    assert 'No space left' not in json.dumps(failure, ensure_ascii=False)


def test_permission_error_points_to_managed_directory_permissions():
    failure = diagnose_failure(PermissionError('denied: /secret/models/model.pkl'), 'model_validation')
    assert failure['code'] == 'permission_denied'
    assert failure['stage_label'] == '模型保存与校验'
    assert '读写权限' in failure['recovery_action']
    assert '/secret' not in json.dumps(failure, ensure_ascii=False)


def test_sqlite_busy_and_corrupt_are_distinguished():
    busy = diagnose_failure(sqlite3.OperationalError('database is locked'), 'data_loading')
    corrupt = diagnose_failure(sqlite3.DatabaseError('file is not a database: /private/a.db'), 'data_loading')
    assert busy['code'] == 'database_busy'
    assert busy['retryable'] is True
    assert '等待' in busy['recovery_action']
    assert corrupt['code'] == 'database_corrupt'
    assert corrupt['retryable'] is False
    assert '备份' in corrupt['recovery_action']
    assert '/private' not in json.dumps(corrupt, ensure_ascii=False)


def test_algorithm_timeout_and_missing_source_have_specific_guidance():
    timeout = diagnose_failure(AlgorithmTimeoutError('算法子进程超过 1800 秒'), 'algorithm_execution')
    missing = diagnose_failure(FileNotFoundError('/private/input/telemetry.csv'), 'data_loading')
    assert timeout['code'] == 'algorithm_timeout'
    assert timeout['retryable'] is True
    assert '缩小' in timeout['recovery_action'] or '减少' in timeout['recovery_action']
    assert missing['code'] == 'input_missing'
    assert '重新导入' in missing['recovery_action']
    assert '/private/input' not in json.dumps(missing, ensure_ascii=False)


def test_generic_failure_is_stage_specific_and_does_not_echo_exception():
    failure = diagnose_failure(RuntimeError('internal stack path=/home/user/token=secret'), 'model_validation')
    assert failure['code'] == 'model_artifact_invalid'
    assert failure['stage'] == 'model_validation'
    assert '模型文件' in failure['message']
    assert '/home/user' not in json.dumps(failure, ensure_ascii=False)
    assert 'secret' not in json.dumps(failure, ensure_ascii=False)


def test_task_public_projection_and_disk_journal_exclude_private_inputs(tmp_path: Path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_training_task(task_id, {
        'algorithm': 'zscore', 'password': 'DO_NOT_PERSIST',
        'dataset_path': '/private/input/telemetry.csv',
    })
    runtime.update_task_progress(task_id, 72, '正在保存模型', stage='model_validation')
    runtime.complete_task(task_id, None, 'failed: /private/input/telemetry.csv',
                          failure=diagnose_failure(PermissionError('denied /private/models'),
                                                    'model_validation'))

    journal = (tmp_path / 'outputs' / 'task_state' / f'{task_id}.json').read_text(encoding='utf-8')
    public = runtime.get_public_task_status(task_id)
    assert 'DO_NOT_PERSIST' not in journal
    assert '/private/input' not in journal
    assert 'denied /private/models' not in journal
    assert 'password' not in public
    assert 'config' not in public
    assert public['stage'] == 'model_validation'
    assert public['failure']['code'] == 'permission_denied'
    assert public['error'] == public['failure']['message']


def test_restarted_running_task_exposes_interrupted_diagnostic(tmp_path: Path):
    task_id = uuid.uuid4().hex
    first = TaskRuntime.for_root(tmp_path)
    first.create_training_task(task_id, {'algorithm': 'zscore', 'private': 'secret'})
    first.update_task_progress(task_id, 42, '正在执行算法', stage='algorithm_execution')

    recovered = TaskRuntime.for_root(tmp_path)
    public = recovered.get_public_task_status(task_id)
    assert public['status'] == 'interrupted'
    assert public['stage'] == 'algorithm_execution'
    assert public['failure']['code'] == 'server_restart'
    assert '不会自动恢复' in public['failure']['message']
    assert '创建一次新任务' in public['failure']['recovery_action']


def test_archive_failure_is_attention_not_model_execution_failure(tmp_path: Path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_training_task(task_id, {'algorithm': 'zscore'})
    runtime.complete_task(task_id, {'status': 'partial', 'archive_status': 'failed',
                                    'archive_error': 'disk full'})
    public = runtime.get_public_task_status(task_id)
    assert public['status'] == 'completed'
    assert public['attention']['code'] == 'archive_failed'
    assert public['failure'] is None


def test_task_operation_error_preserves_stage_and_cause_for_http_adapter():
    from scripts.server.task_diagnostics import TaskOperationError
    cause = AlgorithmTimeoutError('private algorithm worker detail')
    wrapped = TaskOperationError('evaluation_execution', cause, 'evaluation')
    assert wrapped.stage == 'evaluation_execution'
    assert wrapped.task_type == 'evaluation'
    assert wrapped.cause is cause


def test_synchronous_v2_evaluation_failure_returns_safe_structured_diagnostic(monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.task_diagnostics import TaskOperationError

    def fail(*_args, **_kwargs):
        raise TaskOperationError('evaluation_execution',
                                 AlgorithmTimeoutError('worker timeout /private/job'),
                                 'evaluation')
    monkeypatch.setattr(routes, '_evaluation', fail)
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
                           {'http_status': status, **payload})
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/evaluation/run'),
                                  main, b'{}')
    assert response['http_status'] == 400
    assert response['failure']['code'] == 'algorithm_timeout'
    assert response['failure']['stage'] == 'evaluation_execution'
    assert '时间上限' in response['error']
    assert '/private/job' not in json.dumps(response, ensure_ascii=False)


def test_synchronous_score_only_failure_uses_its_own_stage(monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.task_diagnostics import TaskOperationError

    def fail(*_args, **_kwargs):
        raise TaskOperationError('score_only_validation', ValueError('wrong feature schema'), 'score_only')
    monkeypatch.setattr(routes, '_inference', fail)
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
                           {'http_status': status, **payload})
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/inference/run'),
                                  main, b'{}')
    assert response['failure']['code'] == 'selection_invalid'
    assert response['failure']['stage'] == 'score_only_validation'
    assert '数据选择' in response['failure']['recovery_label']


def test_preflight_error_is_structured_without_echoing_internal_path(monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes

    def fail(*_args, **_kwargs):
        raise FileNotFoundError('/private/customer/source.db')
    monkeypatch.setattr(routes, '_training', fail)
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
                           {'http_status': status, **payload})
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/training/start'),
                                  main, b'{}')
    assert response['failure']['code'] == 'input_missing'
    assert response['failure']['stage'] == 'preflight'
    assert '/private/customer' not in json.dumps(response, ensure_ascii=False)


def test_task_type_survives_restart_for_workflow_specific_recovery(tmp_path: Path):
    task_id = uuid.uuid4().hex
    runtime = TaskRuntime.for_root(tmp_path)
    runtime.create_training_task(task_id, {'algorithm': 'zscore'}, task_type='score_only')
    runtime.update_task_progress(task_id, 30, '正在评分', stage='score_only_execution')

    recovered = TaskRuntime.for_root(tmp_path)
    public = recovered.get_public_task_status(task_id)
    assert public['task_type'] == 'score_only'
    assert '仅推理页面' in public['failure']['recovery_action']
    assert public['failure']['recovery_label'] == '重新提交仅推理'


def test_synchronous_evaluation_failure_is_durable_without_request_snapshot(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.task_diagnostics import TaskOperationError

    runtime = TaskRuntime.for_root(tmp_path)
    def fail(*_args, **_kwargs):
        raise TaskOperationError('evaluation_execution',
            AlgorithmTimeoutError('private worker /private/telemetry.csv'), 'evaluation')
    monkeypatch.setattr(routes, '_evaluation', fail)
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
        {'http_status': status, **payload}, record_terminal_task=runtime.record_terminal_task)
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/evaluation/run'), main, b'{}')
    assert response['http_status'] == 400
    assert response['task_id']

    recovered = TaskRuntime.for_root(tmp_path)
    public = recovered.get_public_task_status(response['task_id'])
    assert public['task_type'] == 'evaluation'
    assert public['status'] == 'failed'
    assert public['failure']['code'] == 'algorithm_timeout'
    assert response['task_id'] in {item['task_id'] for item in recovered.list_public_task_statuses()}
    journal = (tmp_path / 'outputs' / 'task_state' / f"{response['task_id']}.json").read_text('utf-8')
    assert '/private/telemetry.csv' not in journal
    assert 'private worker' not in journal
    assert 'config' not in journal


def test_synchronous_score_only_archive_attention_is_durable(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.task_diagnostics import archive_attention

    runtime = TaskRuntime.for_root(tmp_path)
    monkeypatch.setattr(routes, '_inference', lambda *_args, **_kwargs: {
        'status': 'partial', 'archive_status': 'failed', 'archive_attention': archive_attention(),
        'model_id': 7, 'input_contract': {'private_path': '/private/source.csv'}})
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
        {'http_status': status, **payload}, record_terminal_task=runtime.record_terminal_task)
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/inference/run'), main, b'{}')
    assert response['archive_status'] == 'failed'
    assert response['task_id']

    recovered = TaskRuntime.for_root(tmp_path)
    public = recovered.get_public_task_status(response['task_id'])
    assert public['task_type'] == 'score_only'
    assert public['status'] == 'completed'
    assert public['attention']['code'] == 'archive_failed'
    assert response['task_id'] in {item['task_id'] for item in recovered.list_public_task_statuses()}
    journal = (tmp_path / 'outputs' / 'task_state' / f"{response['task_id']}.json").read_text('utf-8')
    assert '/private/source.csv' not in journal


def test_task_history_write_failure_does_not_mask_original_sync_diagnostic(monkeypatch):
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    from scripts.server.task_diagnostics import TaskOperationError

    def fail(*_args, **_kwargs):
        raise TaskOperationError('evaluation_execution', ValueError('invalid feature selection'), 'evaluation')
    monkeypatch.setattr(routes, '_evaluation', fail)
    def broken_recorder(**_kwargs):
        raise KeyError('journal backend detail')
    main = SimpleNamespace(json_response=lambda _handler, payload, status=200:
        {'http_status': status, **payload}, record_terminal_task=broken_recorder)
    response = routes.handle_post(object(), SimpleNamespace(path='/api/v2/evaluation/run'), main, b'{}')
    assert response['http_status'] == 400
    assert response['failure']['code'] == 'algorithm_failed'
    assert 'journal backend detail' not in json.dumps(response, ensure_ascii=False)
    assert 'task_id' not in response
