"""Phase 40: failure injection for durable task states and atomic artifacts."""
from __future__ import annotations

import json
import pickle
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.core import model_manager
from scripts.server.task_runtime import TaskRuntime


class PersistentDetector:
    def __init__(self, label=1):
        self.label = label

    def score(self, rows):
        return [self.label for _ in rows]


class UnpicklableDetector:
    def __reduce__(self):
        raise RuntimeError('injected serialization failure')

    def score(self, rows):
        return [1 for _ in rows]


def test_atomic_write_commit_and_reload(tmp_path):
    store = tmp_path / 'models'
    info = model_manager.save_model('training-01', 'three_sigma', 'data.db', 'v2_channels',
                                    {'f1': .7}, detector=PersistentDetector(), models_dir=store)
    assert Path(info['model_dir']).is_dir()
    assert model_manager.load_model(info['model_dir'], 'three_sigma').score([10, 20]) == [1, 1]
    assert sorted(p.name for p in store.iterdir()) == ['model_training-01']
    assert json.loads((store/'model_training-01'/'metrics.json').read_text(encoding='utf-8')) == {'f1': .7}


def test_failed_serialization_removes_staging_and_does_not_publish(tmp_path):
    store = tmp_path/'models'
    with pytest.raises(RuntimeError, match='持久化'):
        model_manager.save_model('broken-01', 'three_sigma', 'db', 'series', {},
                                 detector=UnpicklableDetector(), models_dir=store)
    assert list(store.iterdir()) == []


def test_json_failure_does_not_leave_partial_artifacts(tmp_path):
    store = tmp_path/'models'
    # Dict keys cannot be tuple keys in JSON. Serialized pickle must be removed.
    with pytest.raises(TypeError):
        model_manager.save_model('broken-json', 'three_sigma', 'db', 'series',
                                 {('tuple',): 1}, detector=PersistentDetector(), models_dir=store)
    assert list(store.iterdir()) == []


def test_duplicate_id_rejected_without_overwriting_previous_model(tmp_path):
    store = tmp_path/'models'
    kwargs = dict(algorithm='three_sigma', dataset='db', table_name='v2_channels',
                  metrics={}, models_dir=store)
    model_manager.save_model('same-model', detector=PersistentDetector(7), **kwargs)
    with pytest.raises(FileExistsError, match='拒绝覆盖'):
        model_manager.save_model('same-model', detector=PersistentDetector(9), **kwargs)
    assert model_manager.load_model(store/'model_same-model', 'three_sigma').score([0]) == [7]
    assert sorted(p.name for p in store.iterdir()) == ['model_same-model']


def test_reload_fails_validation_without_exposing_final_directory(tmp_path, monkeypatch):
    store = tmp_path/'models'
    def fail(*args, **kwargs):
        raise RuntimeError('injected corrupt persisted artifact')
    monkeypatch.setattr(model_manager, 'validate_saved_model', fail)
    with pytest.raises(RuntimeError, match='corrupt'):
        model_manager.save_model('broken-validation', 'three_sigma', 'db', 'series', {},
                                 detector=PersistentDetector(), models_dir=store)
    assert list(store.iterdir()) == []


def test_inflight_task_marked_interrupted_after_restart_without_parameters(tmp_path):
    task_id = uuid.uuid4().hex
    first = TaskRuntime.for_root(tmp_path)
    first.create_training_task(task_id, {'algorithm': 'isolation_forest', 'password': 'SENSITIVE',
                                         'dataset': '/private/input'})
    first.update_task_progress(task_id, 42, 'training')
    path = tmp_path/'outputs'/'task_state'/f'{task_id}.json'
    contents = path.read_text(encoding='utf-8')
    assert 'SENSITIVE' not in contents and '/private/input' not in contents
    recovered = TaskRuntime.for_root(tmp_path)
    task = recovered.get_task_status(task_id)
    assert task['status'] == 'interrupted'
    assert task['progress'] == 42
    assert task['error'] == 'server_restart'
    assert task['config'] == {}
    assert json.loads(path.read_text(encoding='utf-8'))['status'] == 'interrupted'
    with pytest.raises(ValueError, match='duplicate'):
        recovered.create_training_task(task_id, {})


def test_successful_task_persists_safe_receipt_free_projection(tmp_path):
    tid = uuid.uuid4().hex
    runtime = TaskRuntime.for_root(tmp_path)
    runtime.create_training_task(tid, {'token': 'DO_NOT_SAVE'})
    runtime.complete_task(tid, {'experiment_id':'exp-1','status':'success',
                                'execution_receipt':'DO_NOT_SAVE', 'summary': {'raw_values': [1,2]}})
    persisted = (tmp_path/'outputs'/'task_state'/f'{tid}.json').read_text(encoding='utf-8')
    assert 'DO_NOT_SAVE' not in persisted and 'raw_values' not in persisted
    restored = TaskRuntime.for_root(tmp_path).get_task_status(tid)
    assert restored['status'] == 'completed'
    assert restored['result'] == {'experiment_id':'exp-1', 'status':'success'}


def test_corrupt_and_unsafe_recovery_records_are_ignored(tmp_path):
    folder = tmp_path/'outputs'/'task_state'
    folder.mkdir(parents=True)
    (folder/'bad.json').write_text('{broken')
    tid = uuid.uuid4().hex
    (folder/f'{tid}.json').write_text('{"task_id":"not-a-match","status":"running"}')
    runtime = TaskRuntime.for_root(tmp_path)
    assert runtime.tasks == {}


def test_task_state_is_scoped_per_root(tmp_path):
    tid = uuid.uuid4().hex
    left = TaskRuntime.for_root(tmp_path/'left')
    left.create_training_task(tid, {'algorithm':'zscore'})
    right = TaskRuntime.for_root(tmp_path/'right')
    assert right.get_task_status(tid) is None


def test_worker_launch_failure_marks_task_failed(tmp_path, monkeypatch):
    """The synchronous HTTP start path must not strand a running task."""
    from scripts.server import telemetry_v2_routes as routes
    called = []
    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            raise RuntimeError('injected failed thread start')
    monkeypatch.setattr(routes.threading, 'Thread', FakeThread)
    monkeypatch.setattr(routes, '_db', lambda main: tmp_path/'db.sqlite')
    monkeypatch.setattr(routes, 'validate_algorithm_configuration', lambda name, config: {})
    monkeypatch.setattr(routes, 'validate_training_selection', lambda db, config: {
        'feature_keys':['x'], 'runs':[], 'aligned_count':5,'normal_count':5,'fault_count':0,
        'sample_check':{}})
    runtime = TaskRuntime.for_root(tmp_path)
    main = SimpleNamespace(create_training_task=runtime.create_training_task,
                           complete_task=runtime.complete_task)
    request = {'algorithm':'three_sigma','dataset_selection':{}}
    with pytest.raises(RuntimeError, match='thread start'):
        routes._training(main, json.dumps(request).encode())
    assert len(runtime.tasks) == 1
    task = next(iter(runtime.tasks.values()))
    assert task['status'] == 'failed'
    assert '启动失败' in task['error']
    public = runtime.get_public_task_status(task['task_id'])
    assert public['stage'] == 'worker_start'
    assert public['failure']['code'] == 'worker_start_failed'


def test_cleanup_failed_unpublished_artifact_only_in_own_store(tmp_path):
    from scripts.server.training_reliability import cleanup_unpublished_artifact
    store = tmp_path/'models'
    target = store/'model_failure-1'
    target.mkdir(parents=True)
    (target/'model.pkl').write_bytes(b'partial')
    registry = SimpleNamespace(get_model_by_experiment_id=lambda eid: None)
    app = SimpleNamespace(MODEL_REGISTRY=registry, MODELS_DIR=store)
    report = {'experiment_id':'failure-1','model_info':{'model_dir':str(target)}}
    assert cleanup_unpublished_artifact(app, report)
    assert not target.exists()
    target.mkdir()
    app.MODEL_REGISTRY = SimpleNamespace(get_model_by_experiment_id=lambda eid: {'id':1})
    assert not cleanup_unpublished_artifact(app, report)
    assert target.exists()


def test_cleanup_rejects_other_directories_and_symlinks(tmp_path):
    from scripts.server.training_reliability import cleanup_unpublished_artifact
    store=tmp_path/'models'
    store.mkdir()
    sibling=tmp_path/'other'/'model_failure-1'
    sibling.mkdir(parents=True)
    app=SimpleNamespace(MODEL_REGISTRY=SimpleNamespace(get_model_by_experiment_id=lambda eid:None),
                        MODELS_DIR=store)
    assert not cleanup_unpublished_artifact(app, {'experiment_id':'failure-1',
        'model_info':{'model_dir':str(sibling)}})
    assert sibling.is_dir()
    link=store/'model_failure-1'
    try:
        link.symlink_to(sibling, target_is_directory=True)
    except (NotImplementedError, OSError):
        return  # Windows without developer mode
    assert not cleanup_unpublished_artifact(app, {'experiment_id':'failure-1',
        'model_info':{'model_dir':str(link)}})
    assert sibling.is_dir()


def test_task_creation_rolls_back_if_initial_journal_write_fails(tmp_path, monkeypatch):
    runtime = TaskRuntime.for_root(tmp_path)
    tid = uuid.uuid4().hex
    def full_disk(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(runtime, '_write_status', full_disk)
    with pytest.raises(OSError, match='disk full'):
        runtime.create_training_task(tid, {'secret':'not persisted'})
    assert runtime.get_task_status(tid) is None
