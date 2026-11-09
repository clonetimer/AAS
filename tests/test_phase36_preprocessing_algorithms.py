"""Phase 36 contract tests for two extracted domains and mutable runtime roots."""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from scripts import visualization_server as app
from scripts.server import legacy_preprocessing_api, service_algorithms


def test_legacy_preprocess_uses_active_database_and_guards(tmp_path, monkeypatch):
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE telemetry(timestamp TEXT, value REAL, label INTEGER)')
        con.executemany('INSERT INTO telemetry VALUES (?, ?, ?)',
                        [('2026-01-01 00:00:00', 1.0, 0),
                         ('2026-01-01 00:00:01', 2.0, 0),
                         ('2026-01-01 00:00:02', 3.0, 1)])
    monkeypatch.setattr(app, 'SIMULATION_DB_PATH', tmp_path / 'simulation.db')
    result = app.run_data_preprocessing(path, 'telemetry', {})
    assert result['status'] == 'success'
    assert result['rows_processed'] == 3
    status = app.check_clean_table_status(path, 'telemetry')
    assert status['status'] == 'valid' and status['row_count'] == 3
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT timestamp, value, label FROM telemetry_clean ORDER BY timestamp').fetchall() == [
            ('2026-01-01 00:00:00', 0.0, 0),
            ('2026-01-01 00:00:01', 0.5, 0),
            ('2026-01-01 00:00:02', 1.0, 1),
        ]
    assert app.delete_clean_table(path, 'telemetry')['status'] == 'success'
    assert app.check_clean_table_status(path, 'telemetry')['status'] == 'not_exist'


def test_algorithm_import_is_single_implementation_and_explicit_root(tmp_path, monkeypatch):
    # Code-only import has no side effects except a file under the selected root.
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    code = b'from detectors.base import BaseDetector\n'
    result = app.import_algorithm(code, 'temporary_probe.py', 'code_only')
    assert result['code_path'] == 'detectors/temporary_probe.py'
    assert (tmp_path / 'detectors' / 'temporary_probe.py').read_bytes() == code
    separate = app.import_algorithm_separate(b'', '', b'import math\n', 'temporary_probe2.py')
    assert separate['registered'] is False
    assert (tmp_path / 'detectors' / 'temporary_probe2.py').exists()
    with pytest.raises(ValueError, match='不能包含路径'):
        app.import_code_only(b'pass\n', '../escape.py', tmp_path)
    assert not (tmp_path.parent / 'escape.py').exists()


def test_preview_and_errors_match_underlying_algorithm_service():
    raw = 'name: detector_x\nclass_path: detectors.example.Foo\n'
    assert app.preview_algorithm(raw) == service_algorithms.preview_algorithm(raw)
    assert app.preview_algorithm('{}')['valid'] is False
    with pytest.raises(ValueError, match='Unknown import mode'):
        app.import_algorithm(b'', 'file.ext', 'unknown_mode')
    with pytest.raises(ValueError, match='配置文件格式错误'):
        app.import_config_only('[]', 'bad.yaml', Path('/unused'))


def test_compatibility_signatures_and_context_isolation():
    expected = ('preview_algorithm', 'validate_detector_interface', 'import_algorithm',
                'import_from_zip', 'import_code_only', 'import_config_only',
                'import_algorithm_separate', 'check_clean_table_status',
                'run_data_preprocessing', 'delete_clean_table')
    for name in expected:
        fn = getattr(app, name)
        assert callable(fn)
        assert 'app' not in inspect.signature(fn).parameters
    assert app.run_data_preprocessing.__module__ == 'scripts.visualization_server'
    assert legacy_preprocessing_api.run_data_preprocessing.__module__ == 'scripts.server.legacy_preprocessing_api'
