"""P0: one real V2 execution creates an immutable, verifiable report."""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.core.input_evidence import capture_stage, finish_evidence
from scripts.server.execution_provenance import ExecutionRegistry
from scripts.server.v2_experiment_archive import archive_receipt
from scripts.server.task_runtime import TaskRuntime
from scripts.verify_experiment import verify


def _issue(root: Path, registry: ExecutionRegistry, *, kind='evaluation',
           identifier=None, evidence=None, snapshot=None) -> str:
    result = {
        'rows': [{'timestamp': 'frame-1', 'label': 0},
                 {'timestamp': 'frame-2', 'label': 1}],
        'threshold': [0.2, 3.5],
        'decision_threshold': 2.0,
        'pred_labels': [0, 1],
        'metrics': {'accuracy': 0.5},
    }
    if evidence is not None:
        result['input_evidence'] = evidence
    identity = ({'experiment_id': identifier, 'model_dir': 'models/demo',
                 'model_file_path': 'models/demo/model.pkl'} if kind == 'training'
                else {'model_id': 4, 'model_name': 'test model', 'model_algorithm': 'zscore'})
    return registry.issue(kind=kind, config={'algorithm': 'zscore',
                          'dataset': 'sample.db', 'dataset_selection': {'source_ids': [1]}},
                          result=result, identity=identity, root=root,
                          input_evidence=evidence, input_snapshot=snapshot)


def test_archive_writer_verifies_real_execution_and_report_fields():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        registry = ExecutionRegistry()
        receipt = _issue(root, registry)
        archive = archive_receipt(root, registry, kind='evaluation', receipt=receipt)
        assert archive['type'] == 'evaluation'
        assert archive['archive_status'] == 'complete'
        summary = root / archive['summary_path']
        assert summary.is_file()
        data = json.loads(summary.read_text(encoding='utf-8'))
        assert data['experiment']['type'] == 'evaluation'
        assert data['evaluation']['model_id'] == 4
        assert data['results'][0]['metrics'] == {'accuracy': 0.5}
        assert data['results'][0]['chart_trace']['scores'] == [0.2, 3.5]
        assert verify(summary)['status'] == 'verified_archive_digest'
        with gzip.open(summary.parent / 'execution_result.json.gz', 'rt', encoding='utf-8') as handle:
            assert json.load(handle)['pred_labels'] == [0, 1]
        with pytest.raises(ValueError, match='执行凭证'):
            archive_receipt(root, registry, kind='evaluation', receipt=receipt)


def test_archive_writer_never_overwrites_existing_training_run():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        original = root / 'outputs' / 'experiment_original_run'
        original.mkdir(parents=True)
        marker = original / 'keep.txt'
        marker.write_text('old report', encoding='utf-8')
        registry = ExecutionRegistry()
        receipt = _issue(root, registry, kind='training', identifier='original_run')
        with pytest.raises(FileExistsError):
            archive_receipt(root, registry, kind='training', receipt=receipt)
        assert marker.read_text(encoding='utf-8') == 'old report'
        assert list((root / 'outputs').iterdir()) == [original]


def test_archive_writer_persists_complete_input_snapshot():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        bundle = SimpleNamespace(
            train_values=[[1.0]], test_values=[[2.0]],
            train_value_rows=[{'timestamp': 100, 'label': 0}],
            test_value_rows=[{'timestamp': 101, 'label': 1}],
            metadata={'alignment': {'method': 'exact'}}, mode='holdout',
        )
        raw = capture_stage(bundle, 'raw', ['sensor'])
        model = capture_stage(bundle, 'model', ['sensor'])
        evidence, snapshot = finish_evidence(raw, model, bundle, ['sensor'],
                                             {'method': 'min_max'})
        assert evidence['snapshot_status'] == 'complete'
        registry = ExecutionRegistry()
        receipt = _issue(root, registry, evidence=evidence, snapshot=snapshot)
        archive = archive_receipt(root, registry, kind='evaluation', receipt=receipt)
        summary = root / archive['summary_path']
        assert (summary.parent / 'execution_inputs.json.gz').is_file()
        assert verify(summary)['status'] == 'verified_archive_digest'


def test_archive_writer_rejects_unsafe_training_experiment_id():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        registry = ExecutionRegistry()
        receipt = _issue(root, registry, kind='training', identifier='../escape')
        with pytest.raises(ValueError, match='实验编号'):
            archive_receipt(root, registry, kind='training', receipt=receipt)
        assert not (root / 'escape').exists()


def _evaluation_context(root: Path):
    model_dir = root / 'models' / 'demo'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{"trained":true}', encoding='utf-8')
    contract = {
        'version': 7,
        'v2_run_selection': {'source_ids': [6], 'feature_keys': ['sensor'],
                             'method': 'exact', 'tolerance_seconds': 0},
        'v2_run_manifest': {'runs': [{'group_id': 'training-group'}]},
        'model_input_scaling': {'enabled': True, 'method': 'min_max', 'min': [0], 'max': [1]},
        'feature_columns': ['sensor'],
    }
    model = {'id': 3, 'file_path': 'models/demo', 'algorithm': 'zscore',
             'model_name': 'saved model'}
    runtime = TaskRuntime.for_root(root)
    summary = {'result': {'rows': [{'timestamp': 10, 'label': 0},
                                   {'timestamp': 11, 'label': 1}],
                          'threshold': [0.1, 4.2], 'decision_threshold': 3.0,
                          'pred_labels': [0, 1], 'metrics': {'recall': 1.0}}}
    facade = SimpleNamespace(
        ROOT=root, EXECUTION_REGISTRY=runtime.execution_registry,
        get_model=lambda ident: model if ident == 3 else None,
        resolve_managed_model_dir=lambda path: model_dir,
        load_model_config=lambda path: {'config': {'data_contract': contract}},
        update_model_evaluation=lambda ident: None,
        ALGORITHM_SUPERVISOR=SimpleNamespace(run_evaluation=lambda **kwargs: dict(summary)),
    )
    meta = {'runs': [{'source_id': 1}], 'sample_check': {}, 'time_window': [10, 11],
            'method': 'exact', 'label_target': {'scope': 'run'}, 'label_coverage': {}}
    selection = {'source_ids': [1], 'feature_keys': ['sensor'],
                 'method': 'exact', 'tolerance_seconds': 0}
    return facade, meta, selection


def test_route_evaluation_archives_every_execution_for_same_model():
    from scripts.server import telemetry_v2_routes as route
    with TemporaryDirectory() as temp:
        root = Path(temp)
        facade, meta, selection = _evaluation_context(root)
        database = root / 'sample.db'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE observations (value REAL)')
        with (patch.object(route, '_db', return_value=database),
              patch.object(route, 'active_name', return_value='sample.db'),
              patch.object(route, 'load_selection', return_value=([], meta))):
            first = route._evaluation(facade, json.dumps({
                'model_id': 3, 'dataset_selection': selection}).encode())
            second = route._evaluation(facade, json.dumps({
                'model_id': 3, 'dataset_selection': selection}).encode())
        assert first['archive_status'] == second['archive_status'] == 'complete'
        assert first['archive_id'] != second['archive_id']
        assert first['status'] == second['status'] == 'success'
        for result in (first, second):
            summary_path = root / result['summary_path']
            assert verify(summary_path)['status'] == 'verified_archive_digest'
            assert json.loads(summary_path.read_text(encoding='utf-8'))['evaluation']['model_id'] == 3
        assert len(list((root / 'outputs').glob('*/reports/summary.json'))) == 2


def test_route_evaluation_archive_failure_is_explicit_not_a_full_success():
    from scripts.server import telemetry_v2_routes as route
    with TemporaryDirectory() as temp:
        root = Path(temp)
        facade, meta, selection = _evaluation_context(root)
        with (patch.object(route, '_db', return_value=root / 'source.db'),
              patch.object(route, 'active_name', return_value='source.db'),
              patch.object(route, 'load_selection', return_value=([], meta)),
              patch('scripts.server.v2_experiment_archive.archive_receipt',
                    side_effect=OSError('disk full'))):
            result = route._evaluation(facade, json.dumps({
                'model_id': 3, 'dataset_selection': selection}).encode())
        assert result['archive_status'] == 'failed'
        assert result['status'] == 'partial'
        assert result['archive_attention']['code'] == 'archive_failed'
        assert '报告' in result['archive_error']
        assert 'disk full' not in result['archive_error']
        assert '/private' not in result['archive_error']
        assert not list((root / 'outputs').glob('*/reports/summary.json')) if (root / 'outputs').exists() else True


def test_route_training_background_task_auto_archives_before_reporting_complete():
    from scripts.server import telemetry_v2_routes as route
    with TemporaryDirectory() as temp:
        root = Path(temp)
        model_dir = root / 'models' / 'trained'
        model_dir.mkdir(parents=True)
        (model_dir / 'config.json').write_text('{"model":"fake"}', encoding='utf-8')
        db = root / 'source.db'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE source (value REAL)')
        runtime = TaskRuntime.for_root(root)
        summary = {'experiment_id': 'training_20261105',
                   'model_info': {'model_dir': 'models/trained',
                                  'model_file_path': 'models/trained/model.pkl'},
                   'result': {'rows': [{'timestamp': 1, 'label': 0}],
                              'threshold': [0.5], 'pred_labels': [0],
                              'decision_threshold': 2.0, 'metrics': {'f1': 0.8}},
                   'config': {'algorithm': 'three_sigma'}}
        main = SimpleNamespace(
            ROOT=root, EXECUTION_REGISTRY=runtime.execution_registry,
            create_training_task=runtime.create_training_task,
            bind_task_thread=runtime.bind_task_thread,
            update_task_progress=runtime.update_task_progress,
            complete_task=runtime.complete_task,
            ALGORITHM_SUPERVISOR=SimpleNamespace(run_training=lambda **kwargs: dict(summary)),
            resolve_managed_model_dir=lambda path: model_dir,
        )
        preview = {'feature_keys': ['sensor'], 'runs': [{'source_id': 1}],
                   'aligned_count': 8, 'normal_count': 8, 'fault_count': 0,
                   'sample_check': {'valid_samples': 8}}
        payload = {'algorithm': 'three_sigma', 'algorithm_params': {},
                   'dataset_selection': {'source_ids': [1], 'feature_keys': ['sensor']}}
        with (patch.object(route, '_db', return_value=db),
              patch.object(route, 'active_name', return_value='source.db'),
              patch.object(route, 'validate_training_selection', return_value=preview),
              patch.object(route, 'validate_algorithm_configuration', return_value={}),
              patch('scripts.core.model_manager.validate_saved_model', return_value={})):
            accepted = route._training(main, json.dumps(payload).encode())
            assert accepted['status'] == 'accepted'
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                task = runtime.get_task_status(accepted['task_id'])
                if task['status'] != 'running':
                    break
                time.sleep(.01)
        assert task['status'] == 'completed', task
        assert task['result']['archive_status'] == 'complete'
        assert task['result']['archive_id'] == 'training_20261105'
        assert verify(root / task['result']['summary_path'])['status'] == 'verified_archive_digest'
        restored = TaskRuntime.for_root(root)
        assert restored.get_task_status(accepted['task_id'])['status'] == 'completed'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js not available for UI contract')
def test_ui_report_history_defaults_all_and_navigation_is_safe():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(['node', 'tests/v2_auto_archive_ui.cjs'],
                               cwd=root, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'PASS' in completed.stdout


def test_training_failure_records_algorithm_stage_and_safe_diagnostic():
    from scripts.server.algorithm_process import AlgorithmTimeoutError
    from scripts.server import telemetry_v2_routes as route
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'source.db'
        db.touch()
        runtime = TaskRuntime.for_root(root)
        def fail_training(**kwargs):
            raise AlgorithmTimeoutError('algorithm subprocess exceeded 1800 seconds')
        main = SimpleNamespace(
            ROOT=root, EXECUTION_REGISTRY=runtime.execution_registry,
            create_training_task=runtime.create_training_task,
            bind_task_thread=runtime.bind_task_thread,
            update_task_progress=runtime.update_task_progress,
            complete_task=runtime.complete_task,
            ALGORITHM_SUPERVISOR=SimpleNamespace(run_training=fail_training),
            resolve_managed_model_dir=lambda path: root / 'models' / 'missing',
        )
        preview = {'feature_keys':['sensor'], 'runs':[{'source_id':1}],
                   'aligned_count':8, 'normal_count':8, 'fault_count':0,
                   'sample_check':{'valid_samples':8}}
        payload = {'algorithm':'three_sigma','algorithm_params':{},
                   'dataset_selection':{'source_ids':[1],'feature_keys':['sensor']}}
        with (patch.object(route,'_db',return_value=db),
              patch.object(route,'active_name',return_value='source.db'),
              patch.object(route,'validate_training_selection',return_value=preview),
              patch.object(route,'validate_algorithm_configuration',return_value={})):
            accepted=route._training(main,json.dumps(payload).encode())
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                task=runtime.get_public_task_status(accepted['task_id'])
                if task['status']!='running':break
                time.sleep(.01)
        assert task['status']=='failed'
        assert task['stage']=='algorithm_execution'
        assert task['failure']['code']=='algorithm_timeout'
        assert '超时' in task['failure']['title']
        assert '1800 seconds' not in json.dumps(task,ensure_ascii=False)


@pytest.mark.parametrize(('failure_stage', 'expected_code'), [
    ('data_loading', 'input_missing'),
    ('model_validation', 'model_artifact_invalid'),
    ('provenance_capture', 'provenance_failed'),
])
def test_training_failures_keep_the_stage_that_actually_failed(failure_stage, expected_code):
    from scripts.server import telemetry_v2_routes as route
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'source.db'
        db.touch()
        runtime = TaskRuntime.for_root(root)
        model_dir = root / 'models' / 'trained'
        model_dir.mkdir(parents=True)
        (model_dir / 'config.json').write_text('{"algorithm":"three_sigma"}', encoding='utf-8')
        summary = {'experiment_id': 'training_stage_123',
                   'model_info': {'model_dir': 'models/trained',
                                  'model_file_path': 'models/trained/model.pkl'},
                   'result': {'metrics': {'f1': 0.7}}, 'config': {'algorithm': 'three_sigma'}}

        def bind_thread(_task_id):
            if failure_stage == 'data_loading':
                raise FileNotFoundError('/private/source.db')

        def fail_issue(**_kwargs):
            raise RuntimeError('input provenance changed')

        registry = runtime.execution_registry
        if failure_stage == 'provenance_capture':
            registry.issue = fail_issue
        supervisor = SimpleNamespace(run_training=lambda **kwargs: dict(summary))
        main = SimpleNamespace(
            ROOT=root, EXECUTION_REGISTRY=registry,
            create_training_task=runtime.create_training_task,
            bind_task_thread=bind_thread,
            update_task_progress=runtime.update_task_progress,
            complete_task=runtime.complete_task,
            ALGORITHM_SUPERVISOR=supervisor,
            resolve_managed_model_dir=lambda _path: model_dir,
        )
        preview = {'feature_keys': ['sensor'], 'runs': [{'source_id': 1}],
                   'aligned_count': 8, 'normal_count': 8, 'fault_count': 0,
                   'sample_check': {'valid_samples': 8}}
        payload = {'algorithm': 'three_sigma', 'algorithm_params': {},
                   'dataset_selection': {'source_ids': [1], 'feature_keys': ['sensor']}}
        with (patch.object(route, '_db', return_value=db),
              patch.object(route, 'active_name', return_value='source.db'),
              patch.object(route, 'validate_training_selection', return_value=preview),
              patch.object(route, 'validate_algorithm_configuration', return_value={}),
              patch('scripts.core.model_manager.validate_saved_model',
                    side_effect=RuntimeError('invalid artifact') if failure_stage == 'model_validation' else None)):
            accepted = route._training(main, json.dumps(payload).encode())
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                task = runtime.get_public_task_status(accepted['task_id'])
                if task['status'] != 'running':
                    break
                time.sleep(.01)
        assert task['status'] == 'failed'
        assert task['stage'] == failure_stage
        assert task['failure']['code'] == expected_code
        assert '/private' not in json.dumps(task, ensure_ascii=False)


def test_evaluation_algorithm_failure_is_wrapped_with_execution_stage():
    from scripts.server import telemetry_v2_routes as route
    from scripts.server.algorithm_process import AlgorithmTimeoutError
    from scripts.server.task_diagnostics import TaskOperationError
    with TemporaryDirectory() as temp:
        root = Path(temp)
        facade, meta, selection = _evaluation_context(root)
        def fail(**_kwargs):
            raise AlgorithmTimeoutError('timeout /private/eval-worker')
        facade.ALGORITHM_SUPERVISOR = SimpleNamespace(run_evaluation=fail)
        database = root / 'sample.db'
        with (patch.object(route, '_db', return_value=database),
              patch.object(route, 'active_name', return_value='sample.db'),
              patch.object(route, 'load_selection', return_value=([], meta))):
            with pytest.raises(TaskOperationError) as caught:
                route._evaluation(facade, json.dumps({
                    'model_id': 3, 'dataset_selection': selection}).encode())
        assert caught.value.stage == 'evaluation_execution'
        assert isinstance(caught.value.cause, AlgorithmTimeoutError)


def test_score_only_algorithm_failure_is_wrapped_with_its_own_stage(monkeypatch):
    from scripts.server import telemetry_v2_routes as route
    from scripts.server.algorithm_process import AlgorithmTimeoutError
    from scripts.server.task_diagnostics import TaskOperationError
    model = {'id': 5, 'model_name': 'test', 'algorithm': 'zscore'}
    model_dir = Path('/managed/models/test')
    dc = {'feature_columns': ['sensor']}
    selection = {'source_ids': [9], 'feature_keys': ['sensor']}
    expected = {'feature_schema': {'semantics': []}}
    meta = {'runs': [{'source_id': 9}]}
    monkeypatch.setattr(route, '_score_only_context', lambda _main, _request:
        (5, model, model_dir, dc, selection, {}, expected, Path('/managed/db.sqlite'), [], meta))
    monkeypatch.setattr(route, 'active_name', lambda: 'db.sqlite')
    def fail(**_kwargs):
        raise AlgorithmTimeoutError('score worker timeout')
    facade = SimpleNamespace(ALGORITHM_SUPERVISOR=SimpleNamespace(run_score_only=fail))
    with pytest.raises(TaskOperationError) as caught:
        route._inference(facade, b'{"model_id":5}')
    assert caught.value.stage == 'score_only_execution'
    assert isinstance(caught.value.cause, AlgorithmTimeoutError)
