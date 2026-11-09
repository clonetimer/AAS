"""Private worker entrypoint invoked only by AlgorithmProcessService."""
from __future__ import annotations
from utils.path_compat import is_relative_to

import argparse
import json
import os
import sys
import tempfile
import time
import threading
import shutil

import psutil
from functools import partial
from pathlib import Path
from types import SimpleNamespace


def _atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix='.write-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _memory_limit(megabytes: int) -> None:
    if megabytes == 0:
        return
    if os.name != 'posix':
        # Windows memory limits need Job Objects; Phase 41 supports wall-clock
        # timeout there, and does not falsely claim an enforced RAM quota.
        return
    import resource
    limit = megabytes * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))



def _resolve_database_reference(root: Path, database_ref: str) -> tuple[str, Path]:
    """Allow only the active history DB or the fixed user CSV workspace DB."""
    from scripts.server.v2_database_config import active_name
    from scripts.server import v2_user_csv_workspace
    allowed_refs = {active_name(), v2_user_csv_workspace.database_reference()}
    if not isinstance(database_ref, str) or database_ref not in allowed_refs:
        raise ValueError('Algorithm worker received an unapproved V2 database reference')
    datasets_root = (Path(root).resolve() / 'datasets').resolve()
    requested_path = datasets_root / Path(database_ref)
    current = requested_path
    while current != datasets_root and datasets_root in current.parents:
        if current.is_symlink():
            raise ValueError('V2 database symlinks are not allowed in algorithm workers')
        current = current.parent
    db_path = requested_path.resolve()
    if not is_relative_to(db_path, datasets_root) or not db_path.is_file():
        raise ValueError('V2 database missing or outside managed directory')
    return database_ref, db_path

def execute(envelope: dict, workspace: Path, action: str) -> dict:
    from scripts.server.evaluation_workflow import run_evaluation
    from scripts.core.training import TrainingPipeline
    from scripts.core.model_manager import load_model

    root = Path(envelope['root']).resolve()
    models_dir = Path(envelope['models_dir']).resolve()
    if not is_relative_to(models_dir, root):
        raise ValueError('Worker model path outside application')
    payload = envelope['payload']
    database_ref, db_path = _resolve_database_reference(root, payload.get('database'))
    db_name = db_path.name

    def report(task_id, progress, message):
        _atomic_json(workspace / 'progress.json', {'progress': progress, 'message': str(message)[:240]})

    def prohibit_legacy(*args, **kwargs):
        raise ValueError('Only V2 labeled-run execution is permitted in isolated worker')

    app = SimpleNamespace(
        resolve_managed_database_path=lambda name: db_path if name in (database_ref, db_name) else prohibit_legacy(),
        _base_table_name=lambda name: name[:-6] if name and name.endswith('_clean') else name,
        _resolve_clean_table=prohibit_legacy,
        active_name=lambda: db_name,
        TrainingPipeline=partial(TrainingPipeline, models_dir=models_dir),
        update_task_progress=report,
        console_log=lambda message: None,
    )
    common = dict(dataset=database_ref, table='v2_channels', algorithm=payload['algorithm'],
                  v2_run_selection=payload['selection'],
                  alignment_method=payload['selection'].get('method', 'exact'),
                  alignment_tolerance_seconds=payload['selection'].get('tolerance_seconds', 0))
    if action == 'training':
        summary = run_evaluation(app, **common,
            train_ratio=payload['train_ratio'],
            algorithm_params=payload['algorithm_params'],
            normalization_method=payload['normalization_method'],
            training_strategy=payload['training_strategy'],
            v2_role='train', v2_expected_sources=payload['expected'], task_id='worker')
    elif action in ('evaluation','score_only'):
        target = Path(payload['model_dir']).resolve()
        if not is_relative_to(target, models_dir) or target == models_dir:
            raise ValueError('Evaluation artifact outside managed model directory')
        detector = load_model(target, payload['algorithm'])
        if detector is None or not callable(getattr(detector, 'score', None)):
            raise ValueError('模型不可评分，禁止测试时重新训练')
        if action == 'score_only':
            from scripts.server.v2_score_only import score_with_published_detector
            summary=score_with_published_detector(db_path,selection=payload['selection'],
                expected=payload['expected'],scaling=payload['scaling'],
                feature_columns=payload['feature_columns'],
                algorithm=payload['algorithm'],detector=detector)
        else:
            summary = run_evaluation(app, **common, train_ratio=1.0,
                preloaded_detector=detector, persist_model=False,
                input_scaling_contract=payload['scaling'],
                feature_columns=payload['feature_columns'],
                v2_role='test', v2_expected_sources=payload['expected'],
                v2_label_target=payload.get('label_target'))
    else:
        raise ValueError('Unrecognized algorithm worker action')
    return summary



def _start_parent_watchdog(envelope: dict, workspace: Path) -> None:
    """Do not let a detached worker continue training after HTTP host dies.

    Parent PID and creation time are checked to defeat PID reuse. The watcher
    is deliberately outside the algorithm execution thread, so a blocked fit
    cannot prevent detecting a dead server.
    """
    parent_pid = int(envelope['parent_pid'])
    parent_created = float(envelope['parent_created'])
    def watch():
        while True:
            time.sleep(.5)
            try:
                parent = psutil.Process(parent_pid)
                if not parent.is_running() or abs(parent.create_time() - parent_created) > .01:
                    raise psutil.NoSuchProcess(parent_pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                # Parent already died: do not leave a detached fitter or
                # temporary training data behind, even on Windows.
                shutil.rmtree(workspace, ignore_errors=True)
                os._exit(90)
    threading.Thread(target=watch, daemon=True, name='aas-worker-parent-watchdog').start()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=('training', 'evaluation', 'score_only'), required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    try:
        # Fixed input cap: job controls are not arbitrary serialized callables.
        raw = sys.stdin.buffer.readline(128_001)
        if len(raw) > 128_000:
            raise ValueError('Worker job specification exceeds limit')
        envelope = json.loads(raw)
        _start_parent_watchdog(envelope, workspace)
        _memory_limit(int(envelope['memory_mb']))
        summary = execute(envelope, workspace, args.action)
        _atomic_json(workspace / 'result.json', {'ok': True, 'summary': summary})
    except Exception as exc:
        _atomic_json(workspace / 'result.json', {
            'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:1000]}'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
