"""Phase 41: bounded subprocess execution for V2 training and evaluation.

Worker processes are crash boundaries, NOT security sandboxes for untrusted
Python plugins. Only the two reviewed V2 job types are accepted by HTTP code.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil


class AlgorithmWorkerError(RuntimeError):
    """An isolated algorithm did not produce a usable result."""


class AlgorithmTimeoutError(AlgorithmWorkerError):
    """Worker exceeded its configured wall clock limit."""


class AlgorithmCancelledError(AlgorithmWorkerError):
    """The user cancelled the job and its worker process tree was stopped."""


def _positive_int(name: str, default: int, upper: int, *, allow_zero: bool = False) -> int:
    value = os.environ.get(name, str(default))
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer') from exc
    if number < (0 if allow_zero else 1) or number > upper:
        raise ValueError(f'{name} is outside supported limits')
    return number


def _spawn_worker(command: list[str], options: dict) -> subprocess.Popen:
    return subprocess.Popen(command, **options)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        # taskkill /T also terminates children created by detector plugins.
        try:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class AlgorithmProcessService:
    """Per-application subprocess supervisor with bounded concurrency.

    One supervisor is created when the HTTP context is bound. Model paths and
    database name are bound by the server, not by arbitrary browser fields.
    """

    def __init__(self, root: Path, models_dir: Path, *, max_parallel: int = 2):
        self.root = Path(root).resolve()
        self.models_dir = Path(models_dir).resolve()
        if not is_relative_to(self.models_dir, self.root):
            raise ValueError('Worker model directory must be inside application root')
        self.semaphore = threading.BoundedSemaphore(max_parallel)
        self.job_dir = self.root / 'outputs' / 'algorithm_jobs'

    def run_training(self, *, algorithm: str, algorithm_params: dict, selection: dict,
                     train_ratio: float, normalization_method: str,
                     training_strategy: str, expected: dict, database: str,
                     progress: Callable[[Any, str], None] | None = None,
                     cancel_requested: Callable[[], bool] | None = None) -> dict:
        return self._run('training', {
            'algorithm': algorithm, 'algorithm_params': algorithm_params,
            'selection': selection, 'train_ratio': train_ratio,
            'normalization_method': normalization_method,
            'training_strategy': training_strategy, 'expected': expected,
            'database': database,
        }, progress, cancel_requested=cancel_requested)

    def run_evaluation(self, *, algorithm: str, selection: dict, expected: dict,
                       scaling: dict, feature_columns: list, model_dir: Path,
                       database: str, label_target: dict | None = None,
                       progress: Callable[[Any, str], None] | None = None,
                       cancel_requested: Callable[[], bool] | None = None) -> dict:
        target = Path(model_dir).resolve()
        if not is_relative_to(target, self.models_dir) or target == self.models_dir:
            raise ValueError('Model artifact is outside the managed model directory')
        return self._run('evaluation', {
            'algorithm': algorithm, 'selection': selection, 'expected': expected,
            'scaling': scaling, 'feature_columns': feature_columns,
            'model_dir': str(target), 'database': database,
            'label_target': label_target,
        }, progress, cancel_requested=cancel_requested)

    def run_score_only(self, *, algorithm: str, selection: dict, expected: dict,
                       scaling: dict, feature_columns: list, model_dir: Path,
                       database: str, progress: Callable[[Any, str], None] | None = None,
                       cancel_requested: Callable[[], bool] | None = None) -> dict:
        target = Path(model_dir).resolve()
        if not is_relative_to(target, self.models_dir) or target == self.models_dir:
            raise ValueError('Model artifact is outside managed model directory')
        return self._run('score_only', {
            'algorithm':algorithm,'selection':selection,'expected':expected,
            'scaling':scaling,'feature_columns':feature_columns,
            'model_dir':str(target),'database':database}, progress, cancel_requested=cancel_requested)

    def _run(self, action: str, payload: dict, progress=None,
             cancel_requested: Callable[[], bool] | None = None) -> dict:
        if action not in ('training', 'evaluation', 'score_only'):
            raise ValueError('Only reviewed V2 algorithm jobs are supported')
        timeout = _positive_int('AAS_ALGORITHM_TIMEOUT_SECONDS', 1800, 86400)
        memory_mb = _positive_int('AAS_ALGORITHM_MEMORY_MB', 0, 262144, allow_zero=True)
        if not self.semaphore.acquire(blocking=False):
            raise AlgorithmWorkerError('算法执行并发已达到上限，请稍后重试')
        try:
            if cancel_requested is not None and cancel_requested():
                raise AlgorithmCancelledError('用户已取消算法任务')
            self.job_dir.mkdir(parents=True, exist_ok=True)
            # The temporary directory stores result/progress only. Request data
            # is transmitted through stdin and never persisted in job files.
            with tempfile.TemporaryDirectory(prefix='.job-', dir=self.job_dir) as tmp:
                workspace = Path(tmp)
                try:
                    workspace.chmod(0o700)
                except OSError:
                    pass  # Windows ACL is determined by the parent account.
                command = [sys.executable, '-m', 'scripts.server.algorithm_worker',
                           '--action', action, '--workspace', str(workspace)]
                envelope = {
                    'root': str(self.root), 'models_dir': str(self.models_dir),
                    'memory_mb': memory_mb, 'payload': payload,
                    'parent_pid': os.getpid(),
                    'parent_created': psutil.Process(os.getpid()).create_time(),
                }
                data = (json.dumps(envelope, ensure_ascii=False, allow_nan=False) + '\n').encode()
                if len(data) > 128_000:
                    raise ValueError('Algorithm task specification is too large')
                env = os.environ.copy()
                source_root = Path(__file__).resolve().parents[2]
                env['PYTHONPATH'] = str(source_root) + os.pathsep + str(self.root) + os.pathsep + env.get('PYTHONPATH', '')
                # Never accept a browser-provided Python interpreter/command.
                spawn = dict(cwd=self.root, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.name == 'nt':
                    spawn['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                else:
                    spawn['start_new_session'] = True
                proc = _spawn_worker(command, spawn)
                deadline = time.monotonic() + timeout
                last_update = None
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(data)
                    proc.stdin.close()
                    while proc.poll() is None:
                        if cancel_requested is not None and cancel_requested():
                            raise AlgorithmCancelledError('用户取消任务，算法子进程已终止')
                        if time.monotonic() >= deadline:
                            raise AlgorithmTimeoutError(f'算法子进程超过 {timeout} 秒，已终止')
                        if memory_mb and _tree_rss(proc.pid) > memory_mb * 1024 * 1024:
                            raise AlgorithmWorkerError(f'算法子进程超过 {memory_mb} MB 内存上限，已终止')
                        current = _read_progress(workspace / 'progress.json')
                        if current is not None and current != last_update:
                            last_update = current
                            if progress is not None:
                                progress(current['progress'], current['message'])
                        time.sleep(.12)
                    if cancel_requested is not None and cancel_requested():
                        raise AlgorithmCancelledError('用户取消任务，算法子进程已终止')
                    current = _read_progress(workspace / 'progress.json')
                    if current is not None and current != last_update and progress is not None:
                        progress(current['progress'], current['message'])
                    result_file = workspace / 'result.json'
                    if proc.returncode != 0 or not result_file.is_file():
                        raise AlgorithmWorkerError(
                            f'算法子进程异常退出（exit={proc.returncode}），未产生完整结果')
                    if result_file.stat().st_size > 96 * 1024 * 1024:
                        raise AlgorithmWorkerError('算法结果超出进程间传输上限')
                    result = json.loads(result_file.read_text(encoding='utf-8'))
                    if not isinstance(result, dict) or not result.get('ok'):
                        reason = result.get('error', 'invalid worker response') if isinstance(result, dict) else 'invalid worker response'
                        raise AlgorithmWorkerError(f'算法执行失败: {reason}')
                    if not isinstance(result.get('summary'), dict):
                        raise AlgorithmWorkerError('算法进程未返回有效结果')
                    return result['summary']
                except BaseException:
                    _terminate_process_tree(proc)
                    raise
        finally:
            self.semaphore.release()


def _read_progress(path: Path) -> dict | None:
    try:
        if path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and isinstance(value.get('progress'), (int, float)) and isinstance(value.get('message'), str):
            return {'progress': value['progress'], 'message': value['message'][:240]}
    except (OSError, ValueError, TypeError):
        pass
    return None


def _tree_rss(pid: int) -> int:
    """Sample worker and children RSS for Windows/POSIX soft memory limits.

    The worker also receives RLIMIT_AS on POSIX, but this sampled guard works
    on Windows where resource.setrlimit is unavailable. It cannot guarantee
    detection of sub-sampling spikes; this is not a hardened resource sandbox.
    """
    try:
        process = psutil.Process(pid)
        members = [process] + process.children(recursive=True)
        total = 0
        for member in members:
            try:
                total += member.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0
