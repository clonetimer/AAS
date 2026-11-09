from pathlib import Path
import sqlite3

from scripts.server import v2_user_csv_workspace as ws


def test_workspace_initializes_complete_v2_schema_without_opening_history(tmp_path):
    history = tmp_path / "datasets" / "aas_telemetry_v2.sqlite"
    history.parent.mkdir(parents=True)
    history.write_bytes(b"historical-db-must-remain-byte-identical")
    before = history.read_bytes()

    path = ws.initialize(tmp_path)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        version = db.execute(
            "SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
    assert version == ("2",)
    assert {"v2_sources", "v2_channels", "v2_segments", "v2_samples",
            "user_csv_datasets", "user_csv_task_refs"} <= tables
    assert history.read_bytes() == before


def test_workspace_repairs_previous_metadata_only_initialization(tmp_path):
    path = ws.database_path(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE user_csv_datasets (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('train','test')),
          sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          metadata_json TEXT NOT NULL, raw_bytes BLOB NOT NULL, created_utc TEXT NOT NULL
        );
        CREATE TABLE user_csv_task_refs (
          task_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
          created_utc TEXT NOT NULL, PRIMARY KEY(task_id,dataset_id)
        );
        """)
    ws.initialize(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT value FROM v2_meta WHERE key='schema_version'").fetchone() == ("2",)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_samples'").fetchone()
