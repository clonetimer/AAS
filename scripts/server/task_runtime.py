"""Phase 38: explicit task lifecycle and resource sampling service.

The task state, lock, resource sampler, and execution registry belong to one
application runtime. The HTTP context binds this service once at bootstrap;
legacy function names in visualization_server remain compatible.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PERSISTABLE_ID = re.compile(r"^[0-9a-fA-F-]{16,40}$")

from scripts.server.execution_provenance import ExecutionRegistry
from scripts.server.task_diagnostics import (
    STAGE_LABELS, archive_attention, diagnose_failure, sanitize_diagnostic,
    server_restart_diagnostic,
)
from utils.task_resources import TaskResourceTracker


def _json_safe(obj: Any) -> Any:
    """Preserve the prior task-result contract, excluding detector instances."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()
                if key != 'detector' and not str(key).startswith('_')}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _csv_task_result_projection(result: Any) -> dict[str, Any]:
    """Persist only bounded CSV import summaries, never raw rows or request data."""
    if not isinstance(result, dict):
        return {}
    allowed_scalars = {
        'status', 'filename', 'role', 'label_status', 'format', 'has_labels',
        'uncompressed_bytes', 'csv_encoding', 'csv_delimiter', 'format_review_required',
        'sampled_rows', 'row_count', 'column_count', 'sha256', 'time_start', 'time_end',
        'source_format', 'original_file_bytes', 'compression', 'source_id',
        'dataset_id', 'storage_scope', 'historical_database_touched', 'workspace_database',
    }
    projected = {key: result[key] for key in allowed_scalars if key in result and
                 isinstance(result[key], (str, int, float, bool))}
    for key in ('columns', 'numeric_columns', 'time_candidates', 'label_candidates'):
        value = result.get(key)
        if isinstance(value, list) and len(value) <= 66:
            if key == 'columns' and value and isinstance(value[0], dict):
                projected[key] = [{field: item[field] for field in
                    ('source_column', 'subsystem', 'device', 'variable', 'unit') if field in item}
                    for item in value if isinstance(item, dict)]
            elif all(isinstance(item, str) and len(item) <= 200 for item in value):
                projected[key] = value
    missing = result.get('missing_by_column')
    if isinstance(missing, dict) and len(missing) <= 64 and all(
            isinstance(key, str) and type(value) is int and value >= 0
            for key, value in missing.items()):
        projected['missing_by_column'] = missing
    channels = result.get('channels')
    if isinstance(channels, list) and len(channels) <= 64:
        projected['channels'] = [{key: item[key] for key in
            ('source_column', 'channel_id', 'segment_id') if key in item}
            for item in channels if isinstance(item, dict)]
    quality = result.get('quality')
    if isinstance(quality, dict):
        quality_keys = ('total_rows', 'row_count', 'column_count', 'time_column', 'label_column',
                        'time_candidates', 'label_candidates', 'numeric_columns',
                        'missing_by_column', 'non_numeric_by_column', 'nonfinite_by_column',
                        'duplicate_times', 'time_reversals', 'sample_period_median_seconds',
                        'gaps_gt_3x_median', 'label_counts', 'issue_count', 'issues_truncated',
                        'source_order_reversals', 'quality_scope')
        safe_quality = {key: quality[key] for key in quality_keys if key in quality and
                        isinstance(quality[key], (str, int, float, bool, dict, list))}
        issues = quality.get('issues')
        if isinstance(issues, list):
            safe_quality['issues'] = [{key: item[key] for key in
                ('line', 'frame', 'column', 'code', 'suggestion') if key in item}
                for item in issues[:20] if isinstance(item, dict)]
        projected['quality'] = safe_quality
    encoded = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode('utf-8')) > 48_000:
        quality = projected.get('quality')
        if isinstance(quality, dict):
            quality.pop('issues', None)
            quality.pop('numeric_columns', None)
        projected.pop('columns', None)
    return projected


@dataclass
class TaskRuntime:
    """One app's task state and accounting, not a process-wide singleton."""

    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: Any = field(default_factory=threading.Lock)
    resource_tracker: Any = None
    execution_registry: Any = field(default_factory=ExecutionRegistry)
    state_dir: Path | None = None
    cancel_events: dict[str, threading.Event] = field(default_factory=dict)

    def enable_recovery(self, root: Path) -> None:
        """Restore prior terminal records; mark abandoned in-flight jobs interrupted.

        Does not resume training: state and algorithm internals cannot be
        checkpointed safely, and replaying fit could corrupt model provenance.
        Only the task's public status projection is persisted (never config,
        telemetry, tokens, detector objects, or one-time execution receipts).
        """
        self.state_dir = Path(root) / 'outputs' / 'task_state'
        if not self.state_dir.exists():
            return
        with self.lock:
            for path in self.state_dir.glob('*.json'):
                task_id = path.stem
                if not _PERSISTABLE_ID.fullmatch(task_id) or path.is_symlink():
                    continue
                try:
                    if path.stat().st_size > 64_000:
                        continue
                    value = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(value, dict) or value.get('task_id') != task_id:
                        continue
                    status = value.get('status')
                    if status not in ('running', 'cancelling', 'completed', 'failed', 'cancelled', 'interrupted'):
                        continue
                    if task_id in self.tasks:
                        continue
                    value['stage'] = value.get('stage') if value.get('stage') in STAGE_LABELS else 'unknown'
                    value['task_type'] = value.get('task_type') if value.get('task_type') in self._task_types() else 'training'
                    value['failure'] = sanitize_diagnostic(value.get('failure'))
                    value['attention'] = sanitize_diagnostic(value.get('attention'))
                    if status in ('running', 'cancelling'):
                        value['status'] = 'interrupted'
                        value['cancellable'] = False
                        value['cancel_reason'] = '服务重启后，原任务不再运行'
                        value['message'] = '服务已重启；原任务已中断，不会自动恢复'
                        value['error'] = 'server_restart'
                        value['failure'] = server_restart_diagnostic(value['stage'], value['task_type'])
                        value['last_update'] = time.time()
                    elif status == 'cancelled':
                        value['cancellable'] = False
                        value['message'] = value.get('message') or '任务已取消'
                    elif status == 'failed' and value['failure'] is None:
                        value['failure'] = diagnose_failure(value.get('error'), value['stage'])
                    value['error'] = value['failure']['code'] if value['failure'] else None
                    value['config'] = {}  # prior parameters are intentionally not stored
                    value.setdefault('logs', [])
                    self.tasks[task_id] = value
                    # Rewrite old journals through the sanitized projection.
                    self._write_status(task_id, value)
                except (OSError, ValueError, TypeError, UnicodeError):
                    logging.warning('Ignoring invalid task recovery record: %s', path.name)

    def _write_status(self, task_id: str, task: dict[str, Any]) -> None:
        if self.state_dir is None or not _PERSISTABLE_ID.fullmatch(task_id):
            return
        # Do not serialize original request parameters, full results, or receipts.
        # The published artifact / experiment report is the durable result source.
        result = task.get('result')
        task_type = task.get('task_type')
        result_projection = (_csv_task_result_projection(result) if task_type == 'csv_import' else
            {key: result[key] for key in ('experiment_id', 'status', 'algorithm',
                                          'archive_status', 'archive_id', 'summary_path')
             if isinstance(result, dict) and key in result and isinstance(result[key], str)})
        projection = {
            'task_id': task_id, 'status': task.get('status'),
            'task_type': task.get('task_type') if task.get('task_type') in self._task_types() else 'training',
            'progress': task.get('progress'),
            'message': str(task.get('message') or '')[:240],
            'created_at': task.get('created_at'),
            'last_update': task.get('last_update'),
            'stage': str(task.get('stage') or 'unknown')[:40],
            'cancellable': bool(task.get('cancellable', False)),
            'cancel_reason': str(task.get('cancel_reason') or '')[:160],
            'error': (task.get('failure') or {}).get('code') if isinstance(task.get('failure'), dict) else None,
            'failure': sanitize_diagnostic(task.get('failure')),
            'attention': sanitize_diagnostic(task.get('attention')),
            'result': result_projection,
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix='.task-', suffix='.tmp', dir=self.state_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(projection, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.state_dir / f'{task_id}.json')
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @classmethod
    def for_root(cls, root: Path) -> 'TaskRuntime':
        runtime = cls(resource_tracker=TaskResourceTracker(Path(root)))
        runtime.enable_recovery(root)
        return runtime

    @staticmethod
    def _task_types() -> tuple[str, ...]:
        return ('training', 'evaluation', 'score_only', 'batch_import', 'csv_import', 'preprocess')

    def bind_task_thread(self, task_id: str) -> None:
        try:
            with self.lock:
                config = self.tasks.get(task_id, {}).get('config', {})
                algorithm = (config.get('algorithm') or config.get('algorithmName')) if isinstance(config, dict) else None
            if self.resource_tracker is not None:
                self.resource_tracker.start(task_id, algorithm=algorithm)
        except (ValueError, OSError):
            logging.exception('Task resource sampling could not start')

    def update_task_progress(self, task_id: str, progress: Any, message: str | None = None,
                             stage: str | None = None) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task.get('status') not in ('running', 'cancelling'):
                return
            task['progress'] = progress
            if task.get('status') == 'cancelling':
                task['message'] = '已请求取消，正在安全停止当前步骤'
            elif message:
                task['message'] = message
            if stage:
                task['stage'] = stage
                if stage in ('model_validation', 'provenance_capture', 'report_archive', 'completed'):
                    task['cancellable'] = False
                    task['cancel_reason'] = '任务已进入不可中断的模型校验或结果归档阶段'
            task['last_update'] = time.time()
            self._write_status(task_id, task)

    def create_task(self, task_id: str, task_type: str,
                    config: dict[str, Any] | None = None) -> None:
        if task_type not in self._task_types():
            raise ValueError('unsupported task type')
        if (not isinstance(task_id, str) or not 1 <= len(task_id) <= 80 or
                any(ord(char) < 32 for char in task_id) or '/' in task_id or '\\' in task_id):
            raise ValueError('invalid task ID')
        with self.lock:
            if task_id in self.tasks:
                raise ValueError('duplicate task ID; refusing to overwrite prior status')
            self.tasks[task_id] = {
                'task_id': task_id, 'status': 'running', 'progress': 0,
                'message': '任务已创建', 'config': config or {}, 'stage': 'queued',
                'task_type': task_type, 'created_at': time.time(), 'last_update': time.time(),
                'logs': [], 'result': None, 'failure': None, 'attention': None,
                'cancellable': True, 'cancel_reason': None,
            }
            self.cancel_events[task_id] = threading.Event()
            try:
                self._write_status(task_id, self.tasks[task_id])
            except Exception:
                self.tasks.pop(task_id, None)
                self.cancel_events.pop(task_id, None)
                raise

    def create_training_task(self, task_id: str, config: dict[str, Any],
                             task_type: str = 'training') -> None:
        """Backward-compatible entrypoint used by older training callers."""
        self.create_task(task_id, task_type, config)

    def request_cancel_task(self, task_id: str) -> dict[str, Any] | None:
        """Request cooperative cancellation; the worker owns the safe stop point."""
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            status = task.get('status')
            if status == 'cancelling':
                return {'task_id': task_id, 'accepted': True, 'already_requested': True,
                        'status': status, 'message': task.get('message')}
            if status not in ('running',):
                return {'task_id': task_id, 'accepted': False, 'status': status,
                        'reason': '任务已结束，不能取消'}
            if not task.get('cancellable', True):
                return {'task_id': task_id, 'accepted': False, 'status': status,
                        'reason': task.get('cancel_reason') or '任务当前阶段不可取消'}
            event = self.cancel_events.setdefault(task_id, threading.Event())
            event.set()
            task['status'] = 'cancelling'
            task['cancellable'] = False
            task['cancel_reason'] = '已请求取消，正在安全停止当前步骤'
            task['message'] = '已请求取消，正在安全停止当前步骤'
            task['last_update'] = time.time()
            self._write_status(task_id, task)
            return {'task_id': task_id, 'accepted': True, 'already_requested': False,
                    'status': 'cancelling', 'message': task['message']}

    def is_cancel_requested(self, task_id: str) -> bool:
        with self.lock:
            event = self.cancel_events.get(task_id)
            return bool(event and event.is_set())

    def set_task_cancellable(self, task_id: str, cancellable: bool,
                             reason: str | None = None) -> bool:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task.get('status') != 'running':
                return False
            task['cancellable'] = bool(cancellable)
            task['cancel_reason'] = (str(reason)[:160] if reason else None) if not cancellable else None
            task['last_update'] = time.time()
            self._write_status(task_id, task)
            return True

    def complete_task(self, task_id: str, result: Any, error: str | None = None,
                      failure: dict[str, Any] | None = None, *, cancelled: bool = False) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task.get('status') in ('completed', 'failed', 'cancelled', 'interrupted'):
                return
            event = self.cancel_events.get(task_id)
            cancellation_won = bool(cancelled or task.get('status') == 'cancelling' or
                                    (event and event.is_set() and task.get('cancellable', True)))
            if cancellation_won:
                final_status = 'cancelled'
                task['status'] = final_status
                task['message'] = '任务已取消'
                task['result'] = None
                task['error'] = None
                task['failure'] = None
                task['attention'] = None
                task['cancellable'] = False
                task['cancel_reason'] = None
            else:
                final_status = 'failed' if error else 'completed'
                task['status'] = final_status
                task['progress'] = 100
                task['result'] = (_csv_task_result_projection(result)
                                  if task.get('task_type') == 'csv_import' else _json_safe(result))
                task['error'] = error  # internal only; public projection never returns it
                task['failure'] = (sanitize_diagnostic(failure) if failure else None)
                if task['failure']:
                    task['stage'] = task['failure']['stage']
                if error and task['failure'] is None:
                    task['failure'] = diagnose_failure(error, task.get('stage', 'unknown'), task.get('task_type', 'training'))
                task['attention'] = (archive_attention() if isinstance(task['result'], dict) and
                                     task['result'].get('archive_status') == 'failed' else None)
                if task['attention']:
                    task['stage'] = 'report_archive'
                elif not error and task['result'] is not None:
                    task['stage'] = 'completed'
                task['cancellable'] = False
                task['cancel_reason'] = None
            task['last_update'] = time.time()
            self._write_status(task_id, task)
            self.cancel_events.pop(task_id, None)
            experiment_id = (task.get('result') or {}).get('experiment_id') if isinstance(task.get('result'), dict) else None

        try:
            summary = (self.resource_tracker.finish(
                task_id, status=final_status, experiment_id=experiment_id)
                if self.resource_tracker is not None else None)
        except (OSError, RuntimeError, ValueError):
            summary = None
            logging.exception('Task resource summary not available')
        if summary is not None:
            with self.lock:
                task = self.tasks.get(task_id)
                if task is not None and task.get('status') == final_status:
                    task['resource_metrics'] = summary
                    self._write_status(task_id, task)

    def record_terminal_task(self, task_type: str, status: str, stage: str,
                              message: str, failure: dict[str, Any] | None = None,
                              attention: dict[str, Any] | None = None,
                              result: dict[str, Any] | None = None) -> str:
        """Persist a synchronous V2 operation without retaining its request or data.

        Evaluation and score-only inference currently execute within one HTTP
        request. Their terminal diagnostics still need to survive page refresh
        and server restart, so persist only a new ID, bounded status fields,
        sanitized diagnostics and a tiny allowlisted result summary.
        """
        if task_type not in ('training', 'evaluation', 'score_only'):
            raise ValueError('unsupported task type')
        if status not in ('failed', 'completed'):
            raise ValueError('terminal task status must be failed or completed')
        stage = stage if stage in STAGE_LABELS else 'unknown'
        safe_failure = sanitize_diagnostic(failure)
        safe_attention = sanitize_diagnostic(attention)
        if status == 'failed' and safe_failure is None:
            safe_failure = diagnose_failure(message, stage, task_type)
        if status == 'completed' and safe_attention is None and isinstance(result, dict) and result.get('archive_status') == 'failed':
            safe_attention = archive_attention()
        if safe_failure:
            stage = safe_failure['stage']
            public_message = safe_failure['message']
        elif safe_attention:
            stage = 'report_archive'
            public_message = safe_attention['message']
        else:
            public_message = str(message or '同步任务已结束')[:240]
        allowed = ('experiment_id', 'status', 'algorithm', 'archive_status', 'archive_id')
        safe_result = {}
        if isinstance(result, dict):
            for key in allowed:
                value = result.get(key)
                if isinstance(value, (str, int, float, bool)):
                    safe_result[key] = value
        task_id = uuid.uuid4().hex
        now = time.time()
        task = {
            'task_id': task_id, 'status': status, 'progress': 100,
            'message': public_message, 'config': {}, 'stage': stage,
            'task_type': task_type, 'created_at': now, 'last_update': now,
            'logs': [], 'result': safe_result or None,
            'failure': safe_failure, 'attention': safe_attention,
            'error': safe_failure['code'] if safe_failure else None,
        }
        with self.lock:
            self.tasks[task_id] = task
            try:
                self._write_status(task_id, task)
            except Exception:
                self.tasks.pop(task_id, None)
                raise
        return task_id

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.tasks.get(task_id)

    def get_public_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Return the browser-safe status without request config or raw exceptions."""
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            failure = sanitize_diagnostic(task.get('failure'))
            attention = sanitize_diagnostic(task.get('attention'))
            result = _json_safe(task.get('result'))
            if isinstance(result, dict):
                result.pop('archive_error', None)
                if attention:
                    result['archive_error'] = attention['message']
            stage = task.get('stage') if task.get('stage') in STAGE_LABELS else 'unknown'
            return {
                'task_id': task.get('task_id'),
                'task_type': task.get('task_type') if task.get('task_type') in self._task_types() else 'training',
                'status': task.get('status'),
                'progress': task.get('progress'), 'message': task.get('message'),
                'created_at': task.get('created_at'), 'last_update': task.get('last_update'),
                'stage': stage, 'stage_label': STAGE_LABELS.get(stage, STAGE_LABELS['unknown']),
                'failure': failure, 'attention': attention,
                'cancellable': bool(task.get('cancellable', False)) and task.get('status') == 'running',
                'cancel_requested': task.get('status') == 'cancelling',
                'cancel_reason': task.get('cancel_reason'),
                'error': failure['message'] if failure else None, 'result': result,
            }

    def list_public_task_statuses(self, limit: int = 50, include_all: bool = False) -> list[dict[str, Any]]:
        """List recent actionable tasks, or all recent tasks when explicitly requested."""
        try:
            bounded = max(1, min(100, int(limit)))
        except (TypeError, ValueError, OverflowError):
            bounded = 50
        with self.lock:
            selected = [task for task in self.tasks.values()
                        if include_all or task.get('status') in ('failed', 'interrupted', 'cancelled') or task.get('attention')]
            selected.sort(key=lambda task: float(task.get('last_update') or 0), reverse=True)
            ids = [task.get('task_id') for task in selected[:bounded]]
        output = []
        for task_id in ids:
            public = self.get_public_task_status(task_id)
            if public is None:
                continue
            # History cards need only a compact summary. Full traces and result
            # rows remain available through a verified experiment report.
            result = public.get('result')
            if isinstance(result, dict):
                public['result'] = {key: result[key] for key in (
                    'experiment_id', 'status', 'algorithm', 'dataset', 'table_name',
                    'archive_status', 'archive_id', 'summary_path', 'metrics',
                    'filename', 'format', 'source_format', 'row_count', 'column_count', 'source_id')
                    if key in result}
            output.append(public)
        return output

    def count_public_task_statuses(self, include_all: bool = False) -> int:
        with self.lock:
            if include_all:
                return len(self.tasks)
            return sum(1 for task in self.tasks.values()
                       if task.get('status') in ('failed', 'interrupted', 'cancelled') or task.get('attention'))


def build_task_history_summary(runtime, limit: int = 50) -> dict[str, Any]:
    """Canonical status projection over the same recent window used by home/task centre."""
    try:
        bounded = max(1, min(100, int(limit)))
    except (TypeError, ValueError, OverflowError):
        bounded = 50
    items = runtime.list_public_task_statuses(bounded, include_all=True)
    counts = {'running': 0, 'completed': 0, 'failed': 0, 'cancelled': 0,
              'interrupted': 0, 'attention': 0}
    for item in items:
        status = item.get('status')
        if status in ('running', 'cancelling'):
            counts['running'] += 1
        elif status in counts:
            counts[status] += 1
        if item.get('attention'):
            counts['attention'] += 1
    return {'scope': f'latest_{bounded}', 'sample_count': len(items), 'counts': counts,
            'attention_is_completed_subset': True,
            'total': runtime.count_public_task_statuses(include_all=True)}
