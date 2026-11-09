from __future__ import annotations

import threading
import uuid

from scripts.server.task_runtime import TaskRuntime


def test_cancel_request_is_idempotent_and_does_not_persist_request_payload(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_task(task_id, 'evaluation', {'secret': 'PRIVATE', 'csv': 'raw-data'})

    first = runtime.request_cancel_task(task_id)
    second = runtime.request_cancel_task(task_id)

    assert first['accepted'] is True
    assert second['accepted'] is True
    assert second['already_requested'] is True
    assert runtime.is_cancel_requested(task_id) is True
    public = runtime.get_public_task_status(task_id)
    assert public['status'] == 'cancelling'
    assert public['cancellable'] is False
    assert public['cancel_requested'] is True
    assert 'PRIVATE' not in str(public)
    journal = (tmp_path / 'outputs' / 'task_state' / f'{task_id}.json').read_text('utf-8')
    assert 'PRIVATE' not in journal
    assert 'raw-data' not in journal


def test_cancelled_terminal_state_survives_restart_and_cannot_be_cancelled_again(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_task(task_id, 'score_only', {'secret': 'DO_NOT_SAVE'})
    runtime.request_cancel_task(task_id)
    runtime.complete_task(task_id, None, error='task_cancelled', cancelled=True)

    public = runtime.get_public_task_status(task_id)
    assert public['status'] == 'cancelled'
    assert public['message'] == '任务已取消'
    assert public['result'] is None
    assert runtime.request_cancel_task(task_id)['accepted'] is False

    recovered = TaskRuntime.for_root(tmp_path)
    restored = recovered.get_public_task_status(task_id)
    assert restored['status'] == 'cancelled'
    assert restored['cancellable'] is False
    assert 'DO_NOT_SAVE' not in str(restored)


def test_non_cancellable_stage_rejects_cancel_with_reason(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_task(task_id, 'training', {})
    runtime.set_task_cancellable(task_id, False, reason='模型正在校验，不能中断')

    result = runtime.request_cancel_task(task_id)

    assert result['accepted'] is False
    assert result['reason'] == '模型正在校验，不能中断'
    assert runtime.is_cancel_requested(task_id) is False
    assert runtime.get_public_task_status(task_id)['status'] == 'running'


def test_recovery_marks_cancelling_task_interrupted(tmp_path):
    first = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    first.create_task(task_id, 'evaluation', {})
    first.request_cancel_task(task_id)

    recovered = TaskRuntime.for_root(tmp_path)

    public = recovered.get_public_task_status(task_id)
    assert public['status'] == 'interrupted'
    assert public['failure']['code'] == 'server_restart'
    assert recovered.is_cancel_requested(task_id) is False


def test_all_task_history_can_include_active_and_successful_tasks(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    running_id = uuid.uuid4().hex
    complete_id = uuid.uuid4().hex
    runtime.create_task(running_id, 'csv_import', {})
    runtime.create_task(complete_id, 'evaluation', {})
    runtime.complete_task(complete_id, {'status': 'success', 'experiment_id': 'exp-1'})

    assert runtime.list_public_task_statuses() == []
    assert runtime.count_public_task_statuses() == 0
    all_items = runtime.list_public_task_statuses(include_all=True)
    assert {item['task_id'] for item in all_items} == {running_id, complete_id}
    assert runtime.count_public_task_statuses(include_all=True) == 2


def test_cancel_racing_completion_keeps_single_terminal_state(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    for _ in range(30):
        task_id = uuid.uuid4().hex
        runtime.create_task(task_id, 'evaluation', {})
        barrier = threading.Barrier(2)
        result = {}

        def cancel():
            barrier.wait()
            result['cancel'] = runtime.request_cancel_task(task_id)

        thread = threading.Thread(target=cancel)
        thread.start()
        barrier.wait()
        runtime.complete_task(task_id, {'status': 'success', 'experiment_id': 'exp-race'})
        thread.join(timeout=2)
        assert not thread.is_alive()
        public = runtime.get_public_task_status(task_id)
        assert public['status'] in ('completed', 'cancelled')
        if result['cancel']['accepted']:
            assert public['status'] == 'cancelled'
        else:
            assert public['status'] == 'completed'
        assert runtime.request_cancel_task(task_id)['accepted'] is False


def test_task_status_history_and_confirmed_cancel_http_contract(tmp_path):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from scripts.server.application_context import bind_http_context
    from tests.http_test_handler import AuthorizedTestHandler
    from tests.test_phase38_runtime_services import _scoped_context

    context = _scoped_context(tmp_path / 'http')
    task_id = uuid.uuid4().hex
    context.create_task(task_id, 'evaluation', {'private': 'not-public'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    bind_http_context(server, context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + f'/api/task-status?task_id={task_id}', timeout=8) as response:
            status = json.load(response)
        assert status['task_id'] == task_id
        assert status['status'] == 'running'
        assert 'config' not in status

        with urlopen(base + '/api/task-history?limit=10&include_all=1', timeout=8) as response:
            history = json.load(response)
        assert history['total'] == 1
        assert history['items'][0]['task_id'] == task_id

        payload = json.dumps({'task_id': task_id, 'confirm': True}).encode()
        request = Request(base + '/api/task-cancel', data=payload,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=8) as response:
            cancelled = json.load(response)
        assert cancelled['accepted'] is True
        assert cancelled['status'] == 'cancelling'

        try:
            urlopen(base + '/api/task-status?task_id=missing', timeout=8)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('unknown task ID must return 404')

        request = Request(base + '/api/task-cancel', data=json.dumps({'task_id': task_id}).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            urlopen(request, timeout=8)
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError('cancel must require explicit confirm=true')
    finally:
        server.shutdown()
        thread.join(timeout=8)
        server.server_close()


def test_cancel_rejected_after_algorithm_stage_enters_finalization(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_task(task_id, 'training', {})
    runtime.update_task_progress(task_id, 85, '检查模型文件', stage='model_validation')

    result = runtime.request_cancel_task(task_id)

    assert result['accepted'] is False
    assert '不可中断' in result['reason']
    assert runtime.get_public_task_status(task_id)['status'] == 'running'


def test_progress_updates_cannot_hide_an_accepted_cancel_request(tmp_path):
    runtime = TaskRuntime.for_root(tmp_path)
    task_id = uuid.uuid4().hex
    runtime.create_task(task_id, 'evaluation', {})
    runtime.request_cancel_task(task_id)
    assert runtime.set_task_cancellable(task_id, False, reason='finalizing') is False
    runtime.update_task_progress(task_id, 55, '算法仍在计算', stage='evaluation_execution')

    public = runtime.get_public_task_status(task_id)
    assert public['status'] == 'cancelling'
    assert public['cancel_requested'] is True
    assert '安全停止' in public['message']
