"""Phase 22: task-bound resource tracking; no host-wide numbers sold as task usage."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from utils.task_resources import TaskResourceTracker
from utils.monitor import SystemMonitor

ROOT = Path(__file__).resolve().parents[1]


def test_resource_summary_lifecycle_and_restart(tmp_path):
    tracker = TaskResourceTracker(tmp_path, interval=.05)
    task_id = 'a' * 32
    def run():
        tracker.start(task_id, algorithm='isolation_forest')
        end = time.monotonic() + .16
        while time.monotonic() < end:
            _ = sum(x*x for x in range(400))
        assert tracker.current(task_id)['status'] == 'running'
        return tracker.finish(task_id, status='completed', experiment_id='exp-123')
    result = run()
    assert result['elapsed_seconds'] > 0
    assert result['observed_process_group_peak_rss_bytes'] > 0
    assert result['worker_thread_cpu_seconds'] is not None
    assert result['worker_thread_cpu_seconds'] >= 0
    assert result['algorithm'] == 'isolation_forest'
    assert '共享' in result['accuracy_note']
    assert result['gpu']['available'] in (False, True)
    assert tracker.current(task_id) is None
    saved = json.loads((tmp_path/'outputs'/'task_resources'/f'{task_id}.json').read_text(encoding='utf-8'))
    assert saved['experiment_id'] == 'exp-123'
    restarted = TaskResourceTracker(tmp_path)
    assert restarted.by_experiment('exp-123')['task_id'] == task_id
    assert restarted.recent()[0]['status'] == 'completed'


def test_task_monitor_does_not_leak_request_configuration(tmp_path):
    tracker = TaskResourceTracker(tmp_path, interval=.1)
    task_id = 'b' * 32
    tracker.start(task_id, algorithm='zscore')
    monitor = SystemMonitor()
    data = monitor.get_software_status(tasks={task_id: {
        'status': 'running', 'config': {'token': 'secret-token'},
        'created_at': time.time(), 'progress': 22}}, task_resources=tracker)
    assert 'secret-token' not in json.dumps(data)
    assert data['task_resources'][0]['status'] == 'running'
    tracker.finish(task_id, status='failed')


def test_invalid_task_names_and_safe_unavailable_gpu(tmp_path):
    tracker = TaskResourceTracker(tmp_path, gpu_reader=None)
    for item in ('../../../secret', '', 'x'*70):
        try:
            tracker.start(item)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid task ID was accepted')
    tracker._gpu_loaded = True
    tracker.start('c'*32, algorithm='bad?\\nsecret')
    report = tracker.finish('c'*32, status='completed')
    assert 'secret' not in report['algorithm'] or '\\n' not in report['algorithm']
    assert report['gpu']['available'] is False


def test_ui_and_server_integration_contract():
    monitor_ui = (ROOT/'web/visualization/monitor-v2.js').read_text(encoding='utf-8')
    html = (ROOT/'web/visualization/monitor.html').read_text(encoding='utf-8')
    report = (ROOT/'web/visualization/reports.js').read_text(encoding='utf-8')
    app = (ROOT/'scripts/visualization_server.py').read_text(encoding='utf-8')
    handler = (ROOT/'scripts/server/handlers.py').read_text(encoding='utf-8')
    v2 = (ROOT/'scripts/server/telemetry_v2_routes.py').read_text(encoding='utf-8')
    assert '训练任务资源记录' in html and 'monitorResourceRows' in html
    assert 'task_resources' in monitor_ui
    assert 'worker_thread_cpu_seconds' in monitor_ui
    assert 'resource_metrics' in report
    assert 'TASK_RESOURCE_TRACKER.finish' in app
    assert 'main.bind_task_thread(task_id)' in v2
    assert 'task_resources=' in handler


def test_app_training_task_resource_is_attached(tmp_path, monkeypatch):
    from scripts import visualization_server as app
    tracker = TaskResourceTracker(tmp_path, interval=.05)
    monkeypatch.setattr(app, 'TASK_RESOURCE_TRACKER', tracker)
    monkeypatch.setattr(app, 'TRAINING_TASKS', {})
    key = 'd' * 32
    app.create_training_task(key, {'algorithm': 'three_sigma', 'password': 'not_for_monitoring'})
    app.bind_task_thread(key)
    app.update_task_progress(key, 75, '模型运行中')
    time.sleep(.09)
    app.complete_task(key, {'experiment_id': 'training-2026', 'metrics': {'accuracy': .8}})
    record = app.get_task_status(key)
    assert record['status'] == 'completed'
    assert record['resource_metrics']['experiment_id'] == 'training-2026'
    assert tracker.by_experiment('training-2026') is not None
    monitor = SystemMonitor()
    output = monitor.get_software_status(tasks=app.TRAINING_TASKS, task_resources=tracker)
    assert output['task_resources'][0]['status'] == 'completed'
    assert 'not_for_monitoring' not in json.dumps(output)


def test_gpu_observation_stub_is_optional_and_not_exclusive(tmp_path):
    tracker = TaskResourceTracker(tmp_path, interval=.05)
    tracker._nvidia = lambda pids: {
        'supported': True, 'process_memory_bytes': 256 * 1024 * 1024,
        'device_utilization_percent': 75.0,
    }
    key = 'e'*32
    tracker.start(key)
    result = tracker.finish(key, status='completed')
    assert result['gpu']['available'] is True
    assert result['gpu']['observed_process_memory_peak_bytes'] == 256 * 1024 * 1024
    assert result['gpu']['observed_device_utilization_peak_percent'] == 75
    assert '整卡' in result['accuracy_note']


def test_monitor_http_exposes_persisted_resource_summary(tmp_path, monkeypatch):
    """The standard authenticated-routing test fixture exercises the live HTTP API."""
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from tests.http_test_handler import AuthorizedTestHandler
    from scripts import visualization_server as app
    tracker = TaskResourceTracker(tmp_path, interval=.05)
    monkeypatch.setattr(app, 'TASK_RESOURCE_TRACKER', tracker)
    monkeypatch.setattr(app, 'TRAINING_TASKS', {})
    key = 'f'*32
    app.create_training_task(key, {'algorithm': 'zscore'})
    app.bind_task_thread(key)
    app.complete_task(key, {'experiment_id': 'http-test-exp'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/monitor/software', timeout=8) as response:
            assert response.status == 200
            payload = json.load(response)
        matching = [t for t in payload['task_resources'] if t['task_id'] == key]
        assert len(matching) == 1
        assert matching[0]['experiment_id'] == 'http-test-exp'
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def test_report_detail_links_to_task_resource_by_experiment(tmp_path, monkeypatch):
    from scripts import visualization_server as app
    tracker = TaskResourceTracker(tmp_path)
    monkeypatch.setattr(app, 'TASK_RESOURCE_TRACKER', tracker)
    key = '1'*32
    tracker.start(key, algorithm='isolation_forest')
    tracker.finish(key, status='completed', experiment_id='9abf0ae04e80')
    details = app.get_experiment_details('outputs/experiment_9abf0ae04e80/reports/summary.json')
    assert details['experiment']['experiment_id'] == '9abf0ae04e80'
    assert details['resource_metrics']['task_id'] == key
