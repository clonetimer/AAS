"""R7: ordinary Windows permissions and serialized path compatibility.

These tests can run on Linux and on Windows without requiring elevation.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

from scripts import visualization_server as app
from scripts.server import service_algorithms


def test_simulation_delete_denies_busy_file_without_claiming_success(tmp_path, monkeypatch):
    simulated = tmp_path / 'simulation.db'
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app, 'SIMULATION_DB_PATH', simulated)
    monkeypatch.setattr(app, 'HISTORY_SAMPLE_DB_PATH', tmp_path / 'history.db')
    with closing(sqlite3.connect(simulated)) as db:
        with db:
            db.execute('CREATE TABLE telemetry (timestamp REAL, value REAL)')
            db.execute('INSERT INTO telemetry VALUES (1, 2)')
    original_unlink = app.simulation_unlink_with_retry

    def busy(_path):
        raise PermissionError('WinError 32: SQLite file in use')

    monkeypatch.setattr(app, 'simulation_unlink_with_retry', busy)
    refused = app.delete_simulation_db()
    assert refused['deleted'] is False
    assert refused['status'] == 'busy'
    assert simulated.exists()
    assert app.get_simulation_status()['original_tables'] == 1
    monkeypatch.setattr(app, 'simulation_unlink_with_retry', original_unlink)
    accepted = app.delete_simulation_db()
    assert accepted['deleted'] is True and accepted['status'] == 'success'
    assert not simulated.exists()


def test_algorithm_paths_are_url_style_across_platforms(tmp_path):
    result = service_algorithms.import_code_only(
        b'from detectors.base import BaseDetector\n',
        'portable_check.py',
        tmp_path / 'detectors',
        project_root=tmp_path,
    )
    assert result['code_path'] == 'detectors/portable_check.py'
    assert '\\' not in result['code_path']
    assert (tmp_path / 'detectors' / 'portable_check.py').is_file()


def test_all_returned_algorithm_paths_use_posix_format():
    source = (Path(__file__).parents[1] / 'scripts/server/service_algorithms.py').read_text(encoding='utf-8')
    # All user-facing paths in plugin return dictionaries must be OS-neutral.
    assert 'str(dest_path.relative_to(project_root))' not in source
    assert 'str((config_dir / yaml_name).relative_to(project_root))' not in source
    assert 'str((code_dir / normalized_code_name).relative_to(project_root))' not in source
    assert '.relative_to(project_root).as_posix()' in source


def test_r7_portability_checks_are_in_release_gate():
    import json
    stable = json.loads((Path(__file__).parent / 'v2_stable_gate.json').read_text(encoding='utf-8'))['stable_nodes']
    assert any(node.startswith('tests/test_phase53_r7_windows_portability.py::') for node in stable)
