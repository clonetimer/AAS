"""Cross-platform regressions reproducing WinError 32, WinError 1314 and EBADF.

Windows user privileges are NOT required. Handle-lifecycle checks are explicit
so that POSIX pytest cannot accidentally green-light a leaked SQLite handle.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile

import pytest


def _tracked_sqlite(monkeypatch):
    """Connections must be closed explicitly, not only committed by `with`."""
    real_connect = sqlite3.connect
    opened = []

    class TrackedConnection(sqlite3.Connection):
        closed_by_app = False

        def close(self):
            self.closed_by_app = True
            return super().close()

    def connect(*args, **kwargs):
        kwargs.setdefault('factory', TrackedConnection)
        db = real_connect(*args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(sqlite3, 'connect', connect)
    return opened


def _sqlite_file(path, *, blob=False):
    db = sqlite3.connect(path)
    try:
        db.execute('CREATE TABLE logs(time_s INTEGER, "adcs.rw.speed")')
        db.execute('INSERT INTO logs VALUES (?,?)', (0, b'INVALID' if blob else 12.5))
        db.commit()
    finally:
        db.close()
    return path.read_bytes()


def test_import_sqlite_temp_db_closed_on_success_and_rejection(tmp_path, monkeypatch):
    from scripts.server import telemetry_v2_source_import as source
    valid = _sqlite_file(tmp_path / 'valid.sqlite')
    blob = _sqlite_file(tmp_path / 'blob.sqlite', blob=True)
    opened = _tracked_sqlite(monkeypatch)
    real_tempdir = tempfile.TemporaryDirectory

    class WindowsLikeTempDirectory(real_tempdir):
        def cleanup(self):
            # Windows denies unlink while ANY sqlite connection remains open.
            assert all(db.closed_by_app for db in opened), 'Temporary SQLite handle not closed before cleanup'
            super().cleanup()

    monkeypatch.setattr(source.tempfile, 'TemporaryDirectory', WindowsLikeTempDirectory)
    assert source._sqlite_operation(valid, None, inspect=True)['tables'][0]['name'] == 'logs'
    text, _ = source._sqlite_operation(valid, 'logs')
    assert '12.5' in text
    with pytest.raises(ValueError, match='BLOB'):
        source._sqlite_operation(blob, 'logs')
    assert len(opened) == 3
    assert all(db.closed_by_app for db in opened)


def test_model_artifact_fsync_never_uses_read_only_file(tmp_path, monkeypatch):
    from scripts.core import model_manager as mm
    real_open = Path.open
    modes = []
    real_fsync = mm.os.fsync

    def tracked_open(self, mode='r', *args, **kwargs):
        if self.name == 'model.pkl':
            modes.append(mode)
        return real_open(self, mode, *args, **kwargs)

    def winlike_fsync(fd):
        # On Windows, _commit on a read-only file descriptor is EBADF.
        if modes:
            assert modes[-1] != 'rb', 'fsync() of a read-only model file is not Windows-safe'
        return real_fsync(fd)

    monkeypatch.setattr(Path, 'open', tracked_open)
    monkeypatch.setattr(mm.os, 'fsync', winlike_fsync)
    monkeypatch.setattr(mm, 'validate_saved_model', lambda *args, **kwargs: None)
    result = mm._save_model_impl('r6portable', 'zscore', 'fixture', 'v2_channels', {},
                                 detector={'sample': 1}, models_dir=tmp_path)
    assert Path(result['model_file_path']).is_file()
    assert 'r+b' in modes and modes[-1] != 'rb'


def test_snapshot_db_close_also_on_exception(tmp_path, monkeypatch):
    from scripts.rebuild_telemetry_v2 import input_snapshot
    source = tmp_path / 'source.db'
    _sqlite_file(source)
    opened = _tracked_sqlite(monkeypatch)
    with input_snapshot(source) as (db, checksum, size):
        assert db.execute('SELECT COUNT(*) FROM logs').fetchone()[0] == 1
        assert checksum and size > 0
    assert len(opened) == 3
    assert all(db.closed_by_app for db in opened)
    opened.clear()
    with pytest.raises(RuntimeError, match='interruption'):
        with input_snapshot(source):
            raise RuntimeError('interruption')
    assert len(opened) == 3
    assert all(db.closed_by_app for db in opened)


def test_stable_http_fixture_never_requires_symlink_privileges():
    source = (Path(__file__).parent / 'test_v2_training_evaluation_regression_20261020.py').read_text(encoding='utf-8')
    assert '.symlink_to(db)' not in source
    assert 'shutil.copy2(db,' in source


def test_windows_portability_regressions_remain_in_stable_suite():
    import json
    root = Path(__file__).parents[1]
    stable = json.loads((root / 'tests/v2_stable_gate.json').read_text(encoding='utf-8'))['stable_nodes']
    assert 'tests/test_phase53_r6_windows_portability.py::test_import_sqlite_temp_db_closed_on_success_and_rejection' in stable
