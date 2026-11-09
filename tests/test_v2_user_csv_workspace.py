from __future__ import annotations

import hashlib
import sqlite3

import pytest

from scripts.server import v2_user_csv_workspace as ws


def test_persistent_import_list_read_and_sha(tmp_path):
    raw = b'time_s,temp,label\n0,1,0\n1,2,0\n'
    item = ws.import_csv(tmp_path, raw, 'mine.csv', 'train', {'rows': 2}, format_confirmed=True)
    assert ws.database_path(tmp_path).is_file()
    assert item['sha256'] == hashlib.sha256(raw).hexdigest()
    assert ws.list_datasets(tmp_path, 'train')[0]['id'] == item['id']
    assert ws.get_dataset(tmp_path, item['id'], include_bytes=True)['raw_bytes'] == raw
    assert ws.list_datasets(tmp_path, 'test') == []


def test_reject_unconfirmed_wrong_role_and_idempotency_conflict(tmp_path):
    with pytest.raises(ValueError):
        ws.import_csv(tmp_path, b'x', 'x.csv', 'train', {}, format_confirmed=False)
    with pytest.raises(ValueError):
        ws.import_csv(tmp_path, b'x', 'x.csv', 'history', {}, format_confirmed=True)
    ws.import_csv(tmp_path, b'a', 'x.csv', 'train', {}, format_confirmed=True, import_key='k1')
    with pytest.raises(ValueError, match='不同文件或用途'):
        ws.import_csv(tmp_path, b'b', 'x.csv', 'train', {}, format_confirmed=True, import_key='k1')


def test_task_reference_blocks_delete_until_released(tmp_path):
    item = ws.import_csv(tmp_path, b't,x\n0,1\n', 'x.csv', 'test', {}, format_confirmed=True)
    ws.set_import_result(tmp_path, item['id'], {'source_id': None}, status='imported')
    ws.bind_task(tmp_path, 'task-1', item['id'])
    with pytest.raises(RuntimeError):
        ws.delete_dataset(tmp_path, item['id'], confirm=True)
    ws.release_task(tmp_path, 'task-1')
    assert ws.delete_dataset(tmp_path, item['id'], confirm=True)['deleted'] is True


def test_historical_database_is_not_touched(tmp_path):
    historical = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b'protected-historical-db')
    before = historical.read_bytes()
    ws.import_csv(tmp_path, b't,x\n0,1\n', 'x.csv', 'train', {}, format_confirmed=True)
    assert historical.read_bytes() == before
    assert ws.database_path(tmp_path) != historical.resolve()


def test_workspace_initializes_real_v2_schema(tmp_path):
    path = ws.initialize(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()[0] == '2'
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'v2_sources', 'v2_channels', 'v2_segments', 'v2_samples', 'user_csv_datasets'} <= names
