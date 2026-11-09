from pathlib import Path

import pytest

from scripts.server.algorithm_worker import _resolve_database_reference
from scripts.server.v2_user_csv_workspace import database_reference


def test_worker_allows_only_history_and_fixed_user_workspace_databases(tmp_path, monkeypatch):
    from scripts.server import v2_database_config
    monkeypatch.setattr(v2_database_config, 'active_name', lambda: 'aas_telemetry_v2.sqlite')
    datasets = tmp_path / 'datasets'
    (datasets / 'user_csv_workspaces').mkdir(parents=True)
    (datasets / 'aas_telemetry_v2.sqlite').write_bytes(b'history')
    (datasets / database_reference()).write_bytes(b'user-workspace')
    ref, path = _resolve_database_reference(tmp_path, database_reference())
    assert ref == 'user_csv_workspaces/user_csv_workspace.sqlite'
    assert path == (datasets / database_reference()).resolve()
    with pytest.raises(ValueError):
        _resolve_database_reference(tmp_path, '../outside.sqlite')
    with pytest.raises(ValueError):
        _resolve_database_reference(tmp_path, 'some-other.sqlite')


def test_worker_rejects_workspace_symlink(tmp_path, monkeypatch):
    from scripts.server import v2_database_config
    monkeypatch.setattr(v2_database_config, 'active_name', lambda: 'aas_telemetry_v2.sqlite')
    datasets = tmp_path / 'datasets'
    datasets.mkdir()
    outside = tmp_path / 'outside.sqlite'
    outside.write_bytes(b'outside')
    (datasets / 'user_csv_workspaces').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        _resolve_database_reference(tmp_path, database_reference())
