from pathlib import Path

import pytest
from scripts.server import v2_user_csv_workspace as ws
from scripts.server import v2_user_csv_adapter as adapter


def test_commit_uses_workspace_database_and_preserves_history(tmp_path, monkeypatch):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'historical-db-sentinel')
    item = ws.import_csv(tmp_path, b'time_s,temp,label\\n0,1,0\\n', 'mine.csv', 'train',
                         {'columns': ['time_s', 'temp', 'label']}, format_confirmed=True)
    observed = {}

    def fake_import(target_db, source_path, metadata, **kwargs):
        observed['target'] = Path(target_db)
        observed['bytes'] = source_path.read_bytes()
        observed['metadata'] = metadata
        return {'status': 'imported', 'source_id': 42, 'row_count': 1,
                'column_count': 1, 'label_status': 'provided'}

    monkeypatch.setattr('scripts.server.telemetry_v2_source_import.source_operation_file', fake_import)
    result = adapter.commit_dataset(tmp_path, item['id'], {
        'operation': 'commit', 'filename': 'mine.csv',
        'config': {'role': 'train', 'namespace': 'USER_X', 'model': 'M',
                   'time_reference': 'epoch-1', 'selected_columns': ['temp']}
    }, role='train', format_confirmed=True)
    assert observed['target'] == ws.database_path(tmp_path)
    assert observed['bytes'] == b'time_s,temp,label\\n0,1,0\\n'
    assert result['historical_database_touched'] is False
    assert history.read_bytes() == b'historical-db-sentinel'
    assert ws.get_dataset(tmp_path, item['id'])['metadata']['source_id'] == 42


def test_role_mismatch_does_not_import(tmp_path, monkeypatch):
    item = ws.import_csv(tmp_path, b't,x\\n0,1\\n', 'test.csv', 'test', {}, format_confirmed=True)
    called = []
    monkeypatch.setattr('scripts.server.telemetry_v2_source_import.source_operation_file',
                        lambda *a, **k: called.append(True))
    with pytest.raises(ValueError):
        adapter.commit_dataset(tmp_path, item['id'], {
            'operation': 'commit', 'filename': 'test.csv',
            'config': {'role': 'train'}
        }, role='train', format_confirmed=True)
    assert called == []


def test_unconfirmed_format_rejected(tmp_path):
    item = ws.import_csv(tmp_path, b't,x\\n0,1\\n', 'x.csv', 'train', {}, format_confirmed=True)
    with pytest.raises(ValueError):
        adapter.commit_dataset(tmp_path, item['id'], {
            'operation': 'commit', 'filename': 'x.csv', 'config': {'role': 'train'}
        }, role='train', format_confirmed=False)
