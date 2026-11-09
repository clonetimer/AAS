from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from scripts.server.execution_provenance import ExecutionRegistry
from scripts.server.telemetry_v2_routes import _training, _evaluation, _inference, _start_async_v2_task
from scripts.server import v2_user_csv_workspace as ws
from scripts.server.v2_user_csv_adapter import commit_user_csv
from tests.test_phase38_runtime_services import _scoped_context
from tests.test_v2_user_csv_workspace_real_import import _meta


def _wait(main, task_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = main.get_public_task_status(task_id)
        if state and state.get('status') in {'completed', 'failed', 'cancelled', 'interrupted'}:
            return state
        time.sleep(0.05)
    raise AssertionError(f'task {task_id} did not finish within {timeout}s')


def test_route_level_user_csv_train_publish_supervised_eval_and_score_only(tmp_path):
    # The configured historical database is a sentinel, not the user's CSV DB.
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'historical-db-sentinel')
    history_sha = hashlib.sha256(history.read_bytes()).hexdigest()

    main = _scoped_context(tmp_path)
    registry = ExecutionRegistry()
    object.__setattr__(main, 'EXECUTION_REGISTRY', registry)
    main.TASK_RUNTIME.execution_registry = registry

    train_raw = ('time_s,adcs.rw.speed,label\n' + ''.join(
        f'{i},{(i % 7) * 0.1},0\n' for i in range(40))).encode()
    train_meta = _meta(filename='route-train.csv', namespace='USER_CSV_ROUTE_TRAIN')
    train = commit_user_csv(tmp_path, train_raw, 'route-train.csv', train_meta, role='train')
    train_selection = {'source_ids': [train['source_id']], 'feature_keys': ['ADCS.RW1.speed'],
                       'method': 'exact', 'tolerance_seconds': 0}
    train_request = {
        'algorithm': 'zscore', 'algorithm_params': {},
        'dataset_selection': train_selection,
        'data_source': {'type': 'user_csv', 'dataset_id': train['dataset_id']},
        'train_ratio': 0.8, 'normalization_method': 'min_max',
        'training_strategy': 'standard',
    }
    accepted = _training(main, json.dumps(train_request).encode())
    assert accepted['status'] == 'accepted'
    train_task = _wait(main, accepted['task_id'])
    assert train_task['status'] == 'completed', train_task
    train_result = train_task['result']
    assert train_result['data_source_provenance']['workspace_dataset_id'] == train['dataset_id']
    assert train_result['data_source_provenance']['source_sha256'] == hashlib.sha256(train_raw).hexdigest()
    assert ws.list_datasets(tmp_path, 'train')[0]['active_refs'] == 0

    # Publish the trained artifact using the same model registry entry point as
    # the production UI; the saved config must retain user-workspace provenance.
    publish = main.create_model(
        train_result['experiment_id'], 'Route User CSV ZScore', '', 'zscore',
        ws.database_reference(), 'v2_channels', train_result['model_dir'],
        train_result['metrics'], train_result['config'])
    assert publish['status'] == 'success'
    model = next(item for item in main.get_models() if item['experiment_id'] == train_result['experiment_id'])
    assert model['status'] == 'published'
    model_id = model['id']
    model_contract = model['config']['data_contract']
    assert model_contract['data_source_provenance']['workspace_dataset_id'] == train['dataset_id']

    test_raw = ('time_s,adcs.rw.speed,label\n' + ''.join(
        f'{i},{0.2 + (i % 5) * 0.15},{1 if i >= 20 else 0}\n' for i in range(40))).encode()
    test_meta = _meta(role='test', filename='route-test.csv', namespace='USER_CSV_ROUTE_TEST')
    test_meta['config'].pop('unlabeled_test_confirmed', None)
    test_meta['config']['label_column'] = 'label'
    test = commit_user_csv(tmp_path, test_raw, 'route-test.csv', test_meta, role='test')
    test_selection = {'source_ids': [test['source_id']], 'feature_keys': ['ADCS.RW1.speed'],
                      'method': 'exact', 'tolerance_seconds': 0}
    evaluation_request = {
        'model_id': model_id, 'dataset_selection': test_selection,
        'data_source': {'type': 'user_csv', 'dataset_id': test['dataset_id']},
        'label_target': {'scope': 'run'},
    }
    evaluation_accepted = _start_async_v2_task(main, 'evaluation',
        json.dumps(evaluation_request).encode(),
        lambda context, body, task_id: _evaluation(context, body, task_id))
    evaluation_task = _wait(main, evaluation_accepted['task_id'])
    assert evaluation_task['status'] == 'completed', evaluation_task
    evaluation_result = evaluation_task['result']
    assert evaluation_result['data_source_provenance']['workspace_dataset_id'] == test['dataset_id']
    assert ws.list_datasets(tmp_path, 'test')[0]['active_refs'] == 0
    assert main.get_model(model_id)['evaluation_count'] == 1

    unknown_raw = ('time_s,adcs.rw.speed\n' + ''.join(
        f'{i},{10.3 + (i % 3) * 0.1}\n' for i in range(16))).encode()
    unknown_meta = _meta(role='test', filename='route-unknown.csv', namespace='USER_CSV_ROUTE_UNKNOWN')
    unknown = commit_user_csv(tmp_path, unknown_raw, 'route-unknown.csv', unknown_meta, role='test')
    score_selection = {'source_ids': [unknown['source_id']], 'feature_keys': ['ADCS.RW1.speed'],
                       'method': 'exact', 'tolerance_seconds': 0}
    score_request = {
        'model_id': model_id, 'dataset_selection': score_selection,
        'data_source': {'type': 'user_csv', 'dataset_id': unknown['dataset_id']},
    }
    score_accepted = _start_async_v2_task(main, 'score_only',
        json.dumps(score_request).encode(),
        lambda context, body, task_id: _inference(context, body, task_id))
    score_task = _wait(main, score_accepted['task_id'])
    assert score_task['status'] == 'completed', score_task
    score_result = score_task['result']
    assert score_result['evaluation_mode'] == 'score_only'
    assert score_result['data_source_provenance']['workspace_dataset_id'] == unknown['dataset_id']
    assert ws.list_datasets(tmp_path, 'test')[0]['active_refs'] == 0

    assert hashlib.sha256(history.read_bytes()).hexdigest() == history_sha
