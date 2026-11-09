"""AAS process-group and API telemetry; psutil works on Windows/Linux.

Only this server's PID and its live children are included. RSS can double-count
shared pages, and psutil cannot reliably attribute OS network bytes per app.
CPU percent is measured against *one logical CPU* (100% == one full core).
"""
from __future__ import annotations

import os
import platform
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import psutil


class SystemMonitor:
    def __init__(self):
        self.api_calls = {}
        self.api_latency = []
        self.lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._last_sample_at = None
        self._last_processes = {}
        self._started_at = time.time()

    def get_cpu_usage(self):
        return psutil.cpu_percent(interval=None)

    def get_memory_usage(self):
        mem = psutil.virtual_memory()
        return {'total': mem.total, 'available': mem.available, 'used': mem.used, 'percent': mem.percent}

    def get_disk_usage(self):
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        return {'total': disk.total, 'used': disk.used, 'free': disk.free, 'percent': disk.percent}

    def get_network_stats(self):
        net = psutil.net_io_counters()
        if net is None:
            return {'bytes_sent': 0, 'bytes_recv': 0, 'packets_sent': 0, 'packets_recv': 0}
        return {name: getattr(net, name) for name in ('bytes_sent', 'bytes_recv', 'packets_sent', 'packets_recv')}

    @staticmethod
    def _route(endpoint):
        """Exclude query strings and bound path cardinality / sensitive content."""
        path = urlsplit(str(endpoint)).path
        parts = path.split('/')
        clean = []
        for segment in parts:
            if not segment:
                clean.append(segment)
            elif len(segment) > 44 or segment.isdigit():
                clean.append(':id')
            else:
                clean.append(segment[:44])
        return '/'.join(clean)[:140]

    def record_api_call(self, endpoint, latency_ms, *, status=None, method='GET'):
        route = self._route(endpoint)
        if not route.startswith('/api/') or route.startswith(('/api/monitor/', '/api/auth/')):
            return
        key = f'{method} {route}'
        with self.lock:
            if key not in self.api_calls and len(self.api_calls) >= 128:
                key = 'OTHER /api/*'
            row = self.api_calls.setdefault(key, {'count': 0, 'total_latency': 0., 'errors': 0})
            row['count'] += 1
            row['total_latency'] += max(0., float(latency_ms))
            if status is not None and int(status) >= 400:
                row['errors'] += 1
            self.api_latency.append(max(0., float(latency_ms)))
            if len(self.api_latency) > 300:
                del self.api_latency[:-300]

    def get_api_metrics(self):
        with self.lock:
            values = {name: dict(stats) for name, stats in self.api_calls.items()}
            sample = list(self.api_latency)
        return {
            'calls': values,
            'avg_latency_ms': sum(sample) / len(sample) if sample else 0.,
            'total_calls': sum(v['count'] for v in values.values()),
            'total_errors': sum(v['errors'] for v in values.values()),
            'latency_note': '服务端请求处理耗时；不包含网络传输和浏览器渲染',
        }

    @staticmethod
    def _read_process(proc):
        try:
            with proc.oneshot():
                ident = (proc.pid, proc.create_time())
                cpu = proc.cpu_times()
                rss = proc.memory_info().rss
                try:
                    io = proc.io_counters()
                except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError, OSError):
                    io = None
                return {
                    'pid': proc.pid, 'name': proc.name()[:70], 'key': ident,
                    'cpu_seconds': float(cpu.user + cpu.system), 'rss': int(rss),
                    'read_bytes': int(io.read_bytes) if io else None,
                    'write_bytes': int(io.write_bytes) if io else None,
                }
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return None

    def _software_processes(self):
        root = psutil.Process(os.getpid())
        try:
            descendants = root.children(recursive=True)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            descendants = []
        output = []
        for proc in (root, *descendants):
            info = self._read_process(proc)
            if info is not None:
                output.append(info)
        return output

    def get_process_snapshot(self):
        """Measure server and live descendants only. No global process scanning."""
        with self._sample_lock:
            now = time.monotonic()
            rows = self._software_processes()
            baseline = self._last_processes
            elapsed = None if self._last_sample_at is None else max(now - self._last_sample_at, 1e-9)
            cpu_percent = 0.0
            read_rate = 0.0
            write_rate = 0.0
            total_rss = 0
            disk_read = 0
            disk_write = 0
            known_io = False
            current = {}
            for row in rows:
                identity = row['key']
                old = baseline.get(identity)
                cpu_delta = 0.0
                if old is not None and elapsed is not None:
                    cpu_delta = max(0., row['cpu_seconds'] - old['cpu_seconds'])
                    cpu_percent += cpu_delta / elapsed * 100.
                    if row['read_bytes'] is not None and old['read_bytes'] is not None:
                        read_rate += max(0, row['read_bytes'] - old['read_bytes']) / elapsed
                    if row['write_bytes'] is not None and old['write_bytes'] is not None:
                        write_rate += max(0, row['write_bytes'] - old['write_bytes']) / elapsed
                row['cpu_percent'] = cpu_delta / elapsed * 100. if elapsed is not None and old is not None else None
                current[identity] = row
                total_rss += row['rss']
                if row['read_bytes'] is not None:
                    disk_read += row['read_bytes']; known_io = True
                if row['write_bytes'] is not None:
                    disk_write += row['write_bytes']; known_io = True
            self._last_sample_at = now
            self._last_processes = current

        host_memory = psutil.virtual_memory()
        cores = psutil.cpu_count(logical=True) or 1
        processes = [{
            'pid': row['pid'], 'name': row['name'],
            'cpu_percent': round(row['cpu_percent'], 2) if row['cpu_percent'] is not None else None,
            'rss_bytes': row['rss'],
        } for row in sorted(rows, key=lambda r: r['rss'], reverse=True)[:12]]
        return {
            'pid': os.getpid(), 'platform': platform.system(),
            'logical_cores': cores, 'process_count': len(rows),
            'child_process_count': max(0, len(rows) - 1),
            'cpu_percent_one_core': round(cpu_percent, 2) if elapsed is not None else None,
            'cpu_percent_machine': round(cpu_percent / cores, 2) if elapsed is not None else None,
            'memory_rss_bytes': total_rss,
            'host_memory_share_percent': round(total_rss / host_memory.total * 100, 2) if host_memory.total else 0,
            'disk_io': {
                'supported': known_io,
                'read_bytes': disk_read if known_io else None,
                'write_bytes': disk_write if known_io else None,
                'read_rate_bytes_s': round(read_rate, 1) if known_io and elapsed is not None else None,
                'write_rate_bytes_s': round(write_rate, 1) if known_io and elapsed is not None else None,
            },
            'processes': processes,
            'uptime_seconds': max(0, int(time.time() - self._started_at)),
            'scope_note': '仅当前 AAS 服务进程及其存活的子进程；不包括独立启动的其他程序。RSS 汇总可能重复计算共享内存。',
        }

    @staticmethod
    def task_snapshot(tasks=None, task_lock=None):
        if tasks is None:
            return {'running': 0, 'completed': 0, 'failed': 0, 'items': []}

        def collect():
            result = {'running': 0, 'completed': 0, 'failed': 0, 'items': []}
            for key, item in tasks.items():
                status = str(item.get('status', 'unknown'))
                if status in result:
                    result[status] += 1
                if status == 'running':
                    result['items'].append({
                        'id': str(key)[:40], 'status': status,
                        'progress': max(0, min(100, float(item.get('progress', 0)))),
                        'elapsed_seconds': max(0, int(time.time() - item.get('created_at', time.time()))),
                    })
            result['items'] = result['items'][:10]
            return result

        if task_lock is not None:
            with task_lock:
                return collect()
        return collect()

    def get_software_status(self, *, tasks=None, task_lock=None, task_resources=None):
        mem = self.get_memory_usage()
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'software': self.get_process_snapshot(),
            'tasks': self.task_snapshot(tasks, task_lock),
            'task_resources': task_resources.recent(limit=12) if task_resources is not None else [],
            'api': self.get_api_metrics(),
            'host': {'cpu_percent': self.get_cpu_usage(), 'memory_percent': mem['percent'],
                     'memory_total_bytes': mem['total']},
        }

    def get_system_status(self):
        """Backward-compatible host-level endpoint for older consumers."""
        return {
            'timestamp': datetime.now().isoformat(),
            'cpu': self.get_cpu_usage(),
            'memory': self.get_memory_usage(),
            'disk': self.get_disk_usage(),
            'network': self.get_network_stats(),
            'api': self.get_api_metrics(),
        }


monitor = SystemMonitor()
