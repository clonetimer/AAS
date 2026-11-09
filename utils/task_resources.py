"""Per-task resource observations for AAS background training.

CPU is the registered Python worker thread's OS CPU time. RSS and I/O are
*process-group observations during the task*, NOT exclusive task allocations.
NVIDIA GPU memory, when available, is process-scoped and also not exclusive.
Only bounded, sanitized numeric summaries are persisted; no configurations,
tokens, raw process paths or original telemetry are written.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

_ID = re.compile(r'^[0-9a-fA-F-]{16,40}$')


class TaskResourceTracker:
    def __init__(self, root: Path, *, interval: float = 0.5, gpu_reader=None):
        self.root = Path(root)
        self.directory = self.root / 'outputs' / 'task_resources'
        self.interval = max(0.1, float(interval))
        self._lock = threading.RLock()
        self._active = {}
        self._gpu_reader = gpu_reader
        self._gpu_loaded = gpu_reader is not None

    @staticmethod
    def _cpu_for_thread(process, thread_id):
        try:
            return next((float(t.user_time + t.system_time) for t in process.threads()
                         if t.id == thread_id), None)
        except (psutil.Error, OSError):
            return None

    @staticmethod
    def _processes():
        root = psutil.Process(os.getpid())
        try:
            children = root.children(recursive=True)
        except (psutil.Error, OSError):
            children = []
        return [root, *children]

    def _nvidia(self, pids):
        """Read NVML process memory and device utilization if explicitly available.

        Utilization is GPU-device-wide, never a task-specific utilization rate.
        """
        if not self._gpu_loaded:
            try:
                import pynvml  # optional: nvidia-ml-py
                pynvml.nvmlInit()
                self._gpu_reader = pynvml
            except Exception:  # optional dependency, driver and permission failures vary
                self._gpu_reader = None
            self._gpu_loaded = True
        api = self._gpu_reader
        if api is None:
            return {'supported': False, 'process_memory_bytes': None,
                    'device_utilization_percent': None}
        try:
            total = 0
            utils = []
            count = api.nvmlDeviceGetCount()
            if count == 0:
                return {'supported': False, 'process_memory_bytes': None,
                        'device_utilization_percent': None}
            for index in range(count):
                dev = api.nvmlDeviceGetHandleByIndex(index)
                fetch = (getattr(api, 'nvmlDeviceGetComputeRunningProcesses_v3', None)
                         or getattr(api, 'nvmlDeviceGetComputeRunningProcesses', None))
                running = fetch(dev) if fetch else []
                graphics = getattr(api, 'nvmlDeviceGetGraphicsRunningProcesses', None)
                if graphics:
                    running = list(running) + list(graphics(dev))
                seen = set()
                for proc in running:
                    if proc.pid in pids and proc.pid not in seen:
                        amount = getattr(proc, 'usedGpuMemory', None)
                        if isinstance(amount, int) and 0 <= amount < 2**60:
                            total += amount
                        seen.add(proc.pid)
                if seen:  # GPU-level utilization only for devices with an AAS PID
                    util = api.nvmlDeviceGetUtilizationRates(dev)
                    utils.append(float(util.gpu))
            return {'supported': True, 'process_memory_bytes': total,
                    'device_utilization_percent': max(utils) if utils else None}
        except Exception:  # Optional NVML must never break training or monitoring
            return {'supported': False, 'process_memory_bytes': None,
                    'device_utilization_percent': None}

    def _observation(self, worker_id):
        processes = self._processes()
        rss = 0
        read = write = 0
        readable_io = False
        pids = set()
        for proc in processes:
            try:
                pids.add(proc.pid)
                rss += int(proc.memory_info().rss)
                try:
                    io = proc.io_counters()
                    read += int(io.read_bytes)
                    write += int(io.write_bytes)
                    readable_io = True
                except (psutil.Error, AttributeError, OSError):
                    pass
            except (psutil.Error, OSError):
                continue
        cpu = self._cpu_for_thread(processes[0], worker_id)
        gpu = self._nvidia(pids)
        return {'cpu_s': cpu, 'rss_bytes': rss,
                'io_read_bytes': read if readable_io else None,
                'io_write_bytes': write if readable_io else None,
                'gpu_process_memory_bytes': gpu['process_memory_bytes'],
                'gpu_device_utilization_percent': gpu['device_utilization_percent'],
                'gpu_supported': gpu['supported']}

    def start(self, task_id: str, algorithm: str | None = None):
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError('invalid task ID')
        with self._lock:
            if task_id in self._active:
                raise ValueError('duplicate task resource monitor')
            worker_id = threading.get_native_id()
            display_name = str(algorithm or '')[:70]
            display_name = re.sub(r'[^a-zA-Z0-9_+ .-]', '', display_name)
            record = {'task_id': task_id, 'algorithm': display_name, 'worker_thread_id': worker_id,
                      'started_unix': time.time(), 'started_monotonic': time.monotonic(),
                      'samples': 0, 'peak_rss_bytes': 0,
                      'peak_gpu_process_memory_bytes': None,
                      'max_device_gpu_utilization_percent': None,
                      'gpu_supported': False, 'latest': None,
                      'baseline': None, 'stop': threading.Event()}
            self._active[task_id] = record
        self._sample(task_id)
        sampler = threading.Thread(target=self._loop, args=(task_id,),
                                   daemon=True, name='aas-resource-sampler')
        record['sampler'] = sampler
        sampler.start()

    def _sample(self, task_id):
        with self._lock:
            record = self._active.get(task_id)
            if record is None:
                return
            thread_id = record['worker_thread_id']
        observation = self._observation(thread_id)
        with self._lock:
            record = self._active.get(task_id)
            if record is None:
                return
            record['samples'] += 1
            record['latest'] = observation
            if record['baseline'] is None:
                record['baseline'] = observation
            record['peak_rss_bytes'] = max(record['peak_rss_bytes'], observation['rss_bytes'])
            if observation['gpu_supported']:
                record['gpu_supported'] = True
                mem = observation['gpu_process_memory_bytes']
                util = observation['gpu_device_utilization_percent']
                if mem is not None:
                    record['peak_gpu_process_memory_bytes'] = max(
                        record['peak_gpu_process_memory_bytes'] or 0, mem)
                if util is not None:
                    record['max_device_gpu_utilization_percent'] = max(
                        record['max_device_gpu_utilization_percent'] or 0., util)

    def _loop(self, task_id):
        with self._lock:
            record = self._active.get(task_id)
        if record is None:
            return
        while not record['stop'].wait(self.interval):
            self._sample(task_id)

    def _summary(self, record, status, experiment_id=None):
        baseline, current = record['baseline'] or {}, record['latest'] or {}
        duration = max(0., time.monotonic() - record['started_monotonic'])
        a, b = baseline.get('cpu_s'), current.get('cpu_s')
        cpu = max(0., b-a) if a is not None and b is not None else None
        def delta(key):
            start, finish = baseline.get(key), current.get(key)
            return max(0, finish-start) if start is not None and finish is not None else None
        return {
            'schema_version': 1, 'task_id': record['task_id'], 'algorithm': record['algorithm'], 'status': status,
            'experiment_id': str(experiment_id)[:120] if experiment_id else None,
            'started_at': datetime.fromtimestamp(record['started_unix'], tz=timezone.utc).isoformat(),
            'elapsed_seconds': round(duration, 3), 'sample_count': record['samples'],
            'worker_thread_cpu_seconds': round(cpu, 4) if cpu is not None else None,
            'worker_thread_cpu_one_core_percent': round(100*cpu/duration, 2) if cpu is not None and duration else None,
            'observed_process_group_peak_rss_bytes': record['peak_rss_bytes'],
            'observed_process_group_read_bytes': delta('io_read_bytes'),
            'observed_process_group_write_bytes': delta('io_write_bytes'),
            'gpu': {'available': record['gpu_supported'],
                    'observed_process_memory_peak_bytes': record['peak_gpu_process_memory_bytes'],
                    'observed_device_utilization_peak_percent': record['max_device_gpu_utilization_percent']},
            'accuracy_note': 'CPU 仅为任务工作线程；RSS/I/O 是运行期间共享 AAS 进程组观测值；GPU 显存按 AAS 进程统计，设备利用率为整卡值，均非任务独占。',
        }

    def current(self, task_id):
        with self._lock:
            record = self._active.get(task_id)
            return self._summary(record, 'running') if record else None

    def finish(self, task_id, *, status, experiment_id=None):
        with self._lock:
            record = self._active.get(task_id)
            if record is None:
                return None
            record['stop'].set()
        sampler = record.get('sampler')
        if sampler and sampler is not threading.current_thread():
            sampler.join(timeout=1.5)
        self._sample(task_id)
        with self._lock:
            payload = self._summary(record, status, experiment_id)
            self._active.pop(task_id, None)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f'{task_id}.json'
            temp = self.directory / f'.{task_id}.tmp'
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, path)
        except OSError:
            pass  # monitoring failures cannot turn a completed training into a failure
        return payload

    def recent(self, limit=20):
        with self._lock:
            active = [self._summary(row, 'running') for row in self._active.values()]
        saved = []
        if self.directory.exists():
            for path in sorted(self.directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                try:
                    content = json.loads(path.read_text(encoding='utf-8'))
                    if content.get('schema_version') == 1:
                        saved.append(content)
                except (OSError, ValueError):
                    continue
        return (active + saved)[:limit]

    def by_experiment(self, experiment_id):
        if not experiment_id:
            return None
        for item in self.recent(100):
            if item.get('experiment_id') == str(experiment_id):
                return item
        return None
