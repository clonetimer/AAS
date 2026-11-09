"""Phase 21: process-scope monitoring and Linux-compatible frontend contracts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import psutil

from utils.monitor import SystemMonitor

ROOT = Path(__file__).resolve().parents[1]


def test_software_scope_parent_and_children(monkeypatch):
    monitor = SystemMonitor()
    monkeypatch.setattr(monitor, '_software_processes', lambda: [
        {'pid': 100, 'key': (100, 111.0), 'name': 'AAS', 'cpu_seconds': 2., 'rss': 100_000_000, 'read_bytes': 50, 'write_bytes': 20},
        {'pid': 101, 'key': (101, 111.0), 'name': 'trainer', 'cpu_seconds': 1., 'rss': 200_000_000, 'read_bytes': 60, 'write_bytes': 30},
    ])
    monkeypatch.setattr('utils.monitor.time.monotonic', lambda: 1000.0)
    first = monitor.get_process_snapshot()
    assert first['process_count'] == 2 and first['child_process_count'] == 1
    assert first['memory_rss_bytes'] == 300_000_000
    assert first['cpu_percent_one_core'] is None
    monkeypatch.setattr('utils.monitor.time.monotonic', lambda: 1002.0)
    monkeypatch.setattr(monitor, '_software_processes', lambda: [
        {'pid': 100, 'key': (100, 111.0), 'name': 'AAS', 'cpu_seconds': 3., 'rss': 110_000_000, 'read_bytes': 150, 'write_bytes': 220},
        {'pid': 101, 'key': (101, 111.0), 'name': 'trainer', 'cpu_seconds': 4., 'rss': 210_000_000, 'read_bytes': 260, 'write_bytes': 130},
    ])
    current = monitor.get_process_snapshot()
    assert current['cpu_percent_one_core'] == 200
    assert current['memory_rss_bytes'] == 320_000_000
    assert current['disk_io']['read_rate_bytes_s'] == 150
    assert current['disk_io']['write_rate_bytes_s'] == 150


def test_cpu_pid_reuse_or_exit_cannot_produce_negative_spike(monkeypatch):
    monitor = SystemMonitor()
    snapshots = [[{'pid': 100, 'key': (100, 1.), 'name': 'worker', 'cpu_seconds': 10., 'rss': 2, 'read_bytes': None, 'write_bytes': None}],
                 [{'pid': 100, 'key': (100, 2.), 'name': 'worker', 'cpu_seconds': 200., 'rss': 1, 'read_bytes': None, 'write_bytes': None}]]
    monkeypatch.setattr(monitor, '_software_processes', lambda: snapshots.pop(0))
    times = iter((0., 2.))
    monkeypatch.setattr('utils.monitor.time.monotonic', lambda: next(times))
    monitor.get_process_snapshot()
    result = monitor.get_process_snapshot()
    assert result['cpu_percent_one_core'] == 0
    assert result['disk_io']['supported'] is False


def test_api_metrics_ignore_polling_and_sensitive_query():
    monitor = SystemMonitor()
    monitor.record_api_call('/api/monitor/software', 1)
    monitor.record_api_call('/api/auth/login', 2, method='POST')
    monitor.record_api_call('/api/v2/train?secret=token', 4, status=500, method='POST')
    report = monitor.get_api_metrics()
    assert report['total_calls'] == 1 and report['total_errors'] == 1
    assert report['calls']['POST /api/v2/train']['total_latency'] == 4
    assert 'token' not in json.dumps(report)


def test_tasks_no_config_or_secret_exposure():
    monitor = SystemMonitor()
    tasks = {'task1': {'status': 'running', 'progress': 33, 'created_at': 0.,
                       'config': {'secret': 'should_not_leak'}}, 'task2': {'status': 'completed'}}
    result = monitor.get_software_status(tasks=tasks)
    assert result['tasks']['running'] == 1 and result['tasks']['completed'] == 1
    assert 'should_not_leak' not in json.dumps(result)


def test_monitor_page_uses_software_route_and_chinese_labels():
    html = (ROOT / 'web/visualization/monitor.html').read_text(encoding='utf-8')
    code = (ROOT / 'web/visualization/monitor-v2.js').read_text(encoding='utf-8')
    assert 'AAS 软件资源占用' in html
    assert '主机资源（参考）' in html
    assert '进程' in html and '磁盘' in html
    assert '/api/monitor/software' in code
    assert "document.hidden" in code
    assert 'innerHTML' not in code
    assert subprocess.run(['node', '--check', str(ROOT / 'web/visualization/monitor-v2.js')], check=True).returncode == 0


def test_handler_monitor_routes_and_request_metrics():
    code = (ROOT / 'scripts/server/handlers.py').read_text(encoding='utf-8')
    assert 'monitor.get_software_status' in code
    assert 'monitor.record_api_call' in code
    assert 'def do_GET' in code and 'def do_POST' in code


def test_linux_psutil_process_access_is_available():
    monitor = SystemMonitor()
    sample = monitor.get_process_snapshot()
    assert sample['process_count'] >= 1
    assert sample['memory_rss_bytes'] > 0
    assert sample['platform'] in ('Linux', 'Windows', 'Darwin')
