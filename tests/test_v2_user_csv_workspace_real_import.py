from __future__ import annotations

from pathlib import Path

import hashlib
import sqlite3

from scripts.server import v2_user_csv_workspace as ws
from scripts.server.v2_user_csv_adapter import commit_user_csv, inspect_or_preview
from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection


def _meta(role='train', filename='user.csv', namespace='USER_CSV_TEST'):
    config = {
        'namespace': namespace, 'model': 'USER-SAT', 'subsystem': 'ADCS',
        'device': 'RW1', 'variable': 'wide_csv', 'unit': 'rad/s',
        'role': role, 'time_domain': 'numeric_unspecified',
        'time_reference': 'local-csv-run', 'time_unit': 's',
        'column_metadata': {'adcs.rw.speed': {
            'subsystem': 'ADCS', 'device': 'RW1', 'variable': 'speed', 'unit': 'rad/s'}},
        'selected_columns': ['adcs.rw.speed'], 'bind_digest_group': True,
    }
    if role == 'train':
        config['all_normal_confirmed'] = True
    else:
        config['unlabeled_test_confirmed'] = True
    return {'operation': 'commit', 'filename': filename, 'csv_encoding': 'utf-8',
            'csv_delimiter': ',', 'format_confirmed': True, 'config': config}


def test_real_importer_commits_into_user_workspace_and_loaders_can_read_it(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'protected-history-sentinel')
    before = hashlib.sha256(history.read_bytes()).hexdigest()

    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(
        f'{i},{i + 1},0\n' for i in range(20))).encode()
    metadata = _meta()
    workspace_db = ws.database_path(tmp_path)
    ws.initialize(tmp_path)

    # Run the actual file-backed V2 importer in preview mode before commit.
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as tmp:
        staged = Path(tmp) / 'user.csv'
        staged.write_bytes(raw)
        preview_meta = {**metadata, 'operation': 'preview'}
        preview = inspect_or_preview(tmp_path, staged, preview_meta,
            source_digest=hashlib.sha256(raw).hexdigest(), source_size=len(raw), role='train')
    assert preview['status'] == 'preview'
    with sqlite3.connect(workspace_db) as db:
        assert db.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0] == 0

    result = commit_user_csv(tmp_path, raw, 'user.csv', metadata, role='train')
    assert result['status'] == 'imported'
    assert result['historical_database_touched'] is False
    assert result['storage_scope'] == 'user_csv_workspace'
    assert history.read_bytes() == b'protected-history-sentinel'
    assert hashlib.sha256(history.read_bytes()).hexdigest() == before

    datasets = ws.list_datasets(tmp_path, 'train')
    assert len(datasets) == 1
    record = datasets[0]
    assert record['import_status'] == 'imported'
    assert record['source_id'] == result['source_id']
    assert record['sha256'] == hashlib.sha256(raw).hexdigest()
    assert ws.get_dataset(tmp_path, record['id'], include_bytes=True)['raw_bytes'] == raw

    ds = catalog(workspace_db)
    run = next(item for item in ds['runs'] if item['source_id'] == result['source_id'])
    assert run['allowed_role'] == 'train'
    selection = {'source_ids': [result['source_id']],
                 'feature_keys': ['ADCS.RW1.speed'],
                 'method': 'exact', 'tolerance_seconds': 0}
    preview = validate_training_selection(workspace_db, {'dataset_selection': selection})
    rows, contract = load_selection(workspace_db, selection=selection, role='train')
    assert len(rows) == 20
    assert contract['runs'][0]['source_sha256'] == hashlib.sha256(raw).hexdigest()


def test_real_importer_preserves_unknown_labels_for_score_only(tmp_path):
    raw = ('time_s,adcs.rw.speed\n' + ''.join(f'{i},{i + 1}\n' for i in range(20))).encode()
    metadata = _meta(role='test', filename='unknown.csv', namespace='USER_CSV_TEST_ONLY')
    metadata['config'].pop('all_normal_confirmed', None)
    result = commit_user_csv(tmp_path, raw, 'unknown.csv', metadata, role='test')
    assert result['status'] == 'imported'
    assert result['label_status'] == 'unknown'
    db_path = ws.database_path(tmp_path)
    ds = catalog(db_path)
    run = next(item for item in ds['runs'] if item['source_id'] == result['source_id'])
    assert run['allowed_role'] == 'score_only'
    with sqlite3.connect(db_path) as db:
        labels = {row[0] for row in db.execute('''SELECT DISTINCT s.label_int FROM v2_samples s
            JOIN v2_segments g ON g.id=s.segment_id WHERE g.source_id=?''', (result['source_id'],))}
    assert labels == {None}


def test_idempotent_commit_reuses_same_workspace_dataset_and_source(tmp_path):
    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(f'{i},{i + 1},0\n' for i in range(12))).encode()
    metadata = _meta(filename='repeat.csv', namespace='USER_CSV_IDEMPOTENT')
    first = commit_user_csv(tmp_path, raw, 'repeat.csv', metadata, role='train')
    second = commit_user_csv(tmp_path, raw, 'repeat.csv', metadata, role='train')
    assert first['dataset_id'] == second['dataset_id']
    assert first['source_id'] == second['source_id']
    assert len(ws.list_datasets(tmp_path, 'train')) == 1
    with sqlite3.connect(ws.database_path(tmp_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0] == 1


def test_delete_user_csv_removes_logical_source_but_not_history(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'history')
    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(f'{i},{i + 1},0\n' for i in range(12))).encode()
    result = commit_user_csv(tmp_path, raw, 'delete.csv', _meta(filename='delete.csv', namespace='USER_CSV_DELETE'), role='train')
    record_id = result['dataset_id']
    source_id = result['source_id']
    ws.delete_dataset(tmp_path, record_id, confirm=True)
    with sqlite3.connect(ws.database_path(tmp_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM v2_sources WHERE id=?', (source_id,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id WHERE g.source_id=?', (source_id,)).fetchone()[0] == 0
    assert history.read_bytes() == b'history'


def test_actual_algorithm_worker_trains_from_user_workspace_and_persists_provenance(tmp_path):
    import json
    from scripts.server.algorithm_process import AlgorithmProcessService
    from scripts.server.v2_run_dataset import validate_training_selection
    from scripts.server.v2_user_csv_workspace import database_reference

    raw = ('time_s,adcs.rw.speed,label\n' + ''.join(
        f'{i},{(i % 7) * 0.1},0\n' for i in range(40))).encode()
    metadata = _meta(filename='worker-train.csv', namespace='USER_CSV_WORKER_TRAIN')
    imported = commit_user_csv(tmp_path, raw, 'worker-train.csv', metadata, role='train')
    db_path = ws.database_path(tmp_path)
    selection = {'source_ids': [imported['source_id']],
                 'feature_keys': ['ADCS.RW1.speed'],
                 'method': 'exact', 'tolerance_seconds': 0}
    preview = validate_training_selection(db_path, {
        'dataset_selection': selection, 'train_ratio': 0.8})
    expected = {
        'train_source_ids': [run['source_id'] for run in preview['runs']],
        'train_groups': [run['group_id'] for run in preview['runs']],
        'train_run_evidence': preview['runs'],
        'feature_keys': preview['feature_keys'],
        'feature_schema': preview.get('feature_schema'),
        'data_source_provenance': {
            'type': 'user_csv', 'database_ref': database_reference(),
            'workspace_dataset_id': imported['dataset_id'],
            'filename': 'worker-train.csv', 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'role': 'train'},
    }
    supervisor = AlgorithmProcessService(tmp_path, tmp_path / 'models')
    summary = supervisor.run_training(
        algorithm='zscore', algorithm_params={}, selection=selection,
        train_ratio=0.8, normalization_method='min_max',
        training_strategy='standard', expected=expected,
        database=database_reference())
    assert summary['status'] == 'success'
    model_dir = Path(summary['model_info']['model_dir'])
    config = json.loads((model_dir / 'config.json').read_text('utf-8'))
    provenance = config['config']['data_contract']['data_source_provenance']
    assert provenance['type'] == 'user_csv'
    assert provenance['workspace_dataset_id'] == imported['dataset_id']
    assert provenance['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert config['dataset'] == Path(database_reference()).name

    # Publish through the same managed model registry used by the HTTP API.
    from scripts.server.model_registry_service import ModelRegistryService
    from scripts.core.model_manager import validate_saved_model
    registry = ModelRegistryService(tmp_path, model_dir=tmp_path / 'models',
        model_db_path=tmp_path / 'models' / 'model.db', validator=validate_saved_model,
        json_reader=lambda path: json.loads(Path(path).read_text('utf-8')))
    model_info = summary['model_info']
    publish = registry.create_model(summary['experiment_id'], 'User CSV ZScore', '', 'zscore',
        database_reference(), 'v2_channels',
        Path(model_info['model_dir']).relative_to(tmp_path).as_posix(),
        summary['result']['metrics'], config['config'])
    assert publish['status'] == 'success'
    model = registry.get_model_by_experiment_id(summary['experiment_id'])
    assert model and model['status'] == 'published'

    # A labeled CSV is evaluated from the same isolated workspace DB. Its source
    # ID is distinct from the train source; both raw-file digest and run-group
    # leakage checks remain active.
    test_raw = ('time_s,adcs.rw.speed,label\n' + ''.join(
        f'{i},{0.2 + (i % 5) * 0.15},{1 if i >= 20 else 0}\n' for i in range(40))).encode()
    test_meta = _meta(role='test', filename='worker-test.csv', namespace='USER_CSV_WORKER_TEST')
    test_meta['config'].pop('unlabeled_test_confirmed', None)
    test_meta['config']['label_column'] = 'label'
    test_import = commit_user_csv(tmp_path, test_raw, 'worker-test.csv', test_meta, role='test')
    test_selection = {'source_ids': [test_import['source_id']],
                      'feature_keys': ['ADCS.RW1.speed'],
                      'method': 'exact', 'tolerance_seconds': 0}
    test_catalog = catalog(db_path)
    test_run = next(run for run in test_catalog['runs'] if run['source_id'] == test_import['source_id'])
    assert test_run['allowed_role'] == 'test'
    contract = config['config']['data_contract']
    old_selection = contract['v2_run_selection']
    manifest = contract['v2_run_manifest']
    expected_test = {
        'feature_keys': old_selection['feature_keys'],
        'train_source_ids': old_selection['source_ids'],
        'train_source_digests': [run['source_sha256'] for run in manifest['runs']],
        'train_groups': [run['group_id'] for run in manifest['runs']],
        'feature_schema': contract.get('feature_schema'),
    }
    evaluated = supervisor.run_evaluation(
        algorithm='zscore', selection=test_selection, expected=expected_test,
        scaling=contract['model_input_scaling'], feature_columns=contract['feature_columns'],
        model_dir=Path(model_info['model_dir']), database=database_reference(),
        label_target={'scope': 'run'})
    assert evaluated['status'] == 'success'
    assert evaluated['result']['metrics']
    registry.update_model_evaluation(model['id'])
    assert registry.get_model(model['id'])['evaluation_count'] == 1

    # Unlabeled test CSV remains SQL NULL and is scored without supervised metrics.
    unknown_raw = ('time_s,adcs.rw.speed\n' + ''.join(
        f'{i},{10.3 + (i % 3) * 0.1}\n' for i in range(16))).encode()
    unknown_meta = _meta(role='test', filename='worker-unknown.csv', namespace='USER_CSV_WORKER_UNKNOWN')
    unknown_import = commit_user_csv(tmp_path, unknown_raw, 'worker-unknown.csv', unknown_meta, role='test')
    unknown_selection = {'source_ids': [unknown_import['source_id']],
                         'feature_keys': ['ADCS.RW1.speed'],
                         'method': 'exact', 'tolerance_seconds': 0}
    from scripts.server.v2_score_only import load_score_only_rows
    expected_score = {**expected_test,
        'train_source_ids': old_selection['source_ids'],
        'train_source_digests': [run['source_sha256'] for run in manifest['runs']]}
    score_rows, score_meta = load_score_only_rows(db_path, selection=unknown_selection,
        expected=expected_score)
    assert len(score_rows) == 16
    assert score_meta['ground_truth_available'] is False
    scored = supervisor.run_score_only(
        algorithm='zscore', selection=unknown_selection, expected=expected_score,
        scaling=contract['model_input_scaling'], feature_columns=contract['feature_columns'],
        model_dir=Path(model_info['model_dir']), database=database_reference())
    assert scored['status'] == 'success'
    assert scored['result']['ground_truth_available'] is False
