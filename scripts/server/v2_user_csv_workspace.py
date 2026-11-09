"""Persistent, isolated workspace for user-owned training/test CSV files.

This database contains its own V2 logical telemetry schema plus the original
uploaded file registry. It is never the configured historical telemetry DB.
Uploaded bytes are retained until the user explicitly deletes the dataset.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_DB_RELATIVE = Path('user_csv_workspaces') / 'user_csv_workspace.sqlite'
_ID = re.compile(r'^[0-9a-f]{32}$')
_MAX_BYTES = 64 * 1024 * 1024
_TERMINAL_TASK_STATES = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})


def database_path(root: Path) -> Path:
    return (Path(root).resolve() / 'datasets' / _DB_RELATIVE).resolve()


def database_reference() -> str:
    """Safe path relative to datasets/, consumed only by the worker allowlist."""
    return _DB_RELATIVE.as_posix()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def initialize(root: Path) -> Path:
    path = database_path(root)
    with _connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        version = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='v2_meta'").fetchone()
        if not version:
            # A new user workspace must be a real V2 logical-source database;
            # the source importer and run-dataset loaders require schema v2.
            from scripts.rebuild_telemetry_v2 import create_v2
            create_v2(db)
        else:
            row = db.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
            if not row or str(row[0]) != '2':
                raise ValueError('用户 CSV 工作区不是有效的 V2 数据库，拒绝覆盖或自动重建')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS user_csv_datasets (
          id TEXT PRIMARY KEY,
          filename TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('train','test')),
          sha256 TEXT NOT NULL,
          size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
          metadata_json TEXT NOT NULL,
          raw_bytes BLOB NOT NULL,
          created_utc TEXT NOT NULL,
          import_key TEXT UNIQUE,
          import_status TEXT NOT NULL DEFAULT 'uploaded'
            CHECK(import_status IN ('uploaded','importing','imported','failed')),
          source_id INTEGER,
          import_result_json TEXT NOT NULL DEFAULT '{}',
          import_task_id TEXT,
          import_started_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_csv_role_created
          ON user_csv_datasets(role, created_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_user_csv_source_id
          ON user_csv_datasets(source_id);
        CREATE TABLE IF NOT EXISTS user_csv_task_refs (
          task_id TEXT NOT NULL,
          dataset_id TEXT NOT NULL REFERENCES user_csv_datasets(id) ON DELETE RESTRICT,
          created_utc TEXT NOT NULL,
          PRIMARY KEY(task_id,dataset_id)
        );
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(user_csv_datasets)')}
        migrations = {
            'import_key': 'ALTER TABLE user_csv_datasets ADD COLUMN import_key TEXT',
            'import_status': "ALTER TABLE user_csv_datasets ADD COLUMN import_status TEXT NOT NULL DEFAULT 'uploaded'",
            'source_id': 'ALTER TABLE user_csv_datasets ADD COLUMN source_id INTEGER',
            'import_result_json': "ALTER TABLE user_csv_datasets ADD COLUMN import_result_json TEXT NOT NULL DEFAULT '{}'",
            'import_task_id': 'ALTER TABLE user_csv_datasets ADD COLUMN import_task_id TEXT',
            'import_started_utc': 'ALTER TABLE user_csv_datasets ADD COLUMN import_started_utc TEXT',
        }
        for name, statement in migrations.items():
            if name not in columns:
                db.execute(statement)
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_user_csv_import_key ON user_csv_datasets(import_key) WHERE import_key IS NOT NULL')
    return path


def import_csv(root: Path, raw: bytes, filename: str, role: str,
               metadata: dict[str, Any], *, format_confirmed: bool,
               import_key: str | None = None) -> dict[str, Any]:
    if format_confirmed is not True:
        raise ValueError('必须先完成 CSV 格式检查并明确确认')
    if role not in ('train', 'test'):
        raise ValueError('CSV 工作区用途只能是 train 或 test')
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_BYTES:
        raise ValueError('CSV 文件为空或超过 64 MiB 限制')
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 200
            or filename != filename.strip() or '/' in filename or '\\' in filename):
        raise ValueError('文件名无效')
    if not isinstance(metadata, dict):
        raise ValueError('CSV 检查摘要无效')
    if import_key is not None and (not isinstance(import_key, str) or not 1 <= len(import_key) <= 200):
        raise ValueError('CSV 幂等键无效')
    digest = hashlib.sha256(raw).hexdigest()
    created = datetime.now(timezone.utc).isoformat()
    path = initialize(root)
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if import_key:
            existing = db.execute('SELECT * FROM user_csv_datasets WHERE import_key=?', (import_key,)).fetchone()
            if existing:
                if existing['sha256'] != digest or existing['role'] != role or existing['filename'] != filename:
                    raise ValueError('同一 CSV 提交标识对应了不同文件或用途，拒绝重复提交')
                if existing['import_status'] in ('uploaded', 'failed'):
                    db.execute('UPDATE user_csv_datasets SET metadata_json=? WHERE id=?',
                        (json.dumps(metadata, ensure_ascii=False, sort_keys=True), existing['id']))
                    existing = db.execute('SELECT * FROM user_csv_datasets WHERE id=?', (existing['id'],)).fetchone()
                return _public_record(existing)
        dataset_id = uuid.uuid4().hex
        db.execute('''INSERT INTO user_csv_datasets
          (id,filename,role,sha256,size_bytes,metadata_json,raw_bytes,created_utc,import_key,import_status)
          VALUES(?,?,?,?,?,?,?,?,?,'uploaded')''',
          (dataset_id, filename, role, digest, len(raw),
           json.dumps(metadata, ensure_ascii=False, sort_keys=True), raw, created, import_key))
        row = db.execute('SELECT * FROM user_csv_datasets WHERE id=?', (dataset_id,)).fetchone()
    return _public_record(row)


def _public_record(row: sqlite3.Row | dict) -> dict[str, Any]:
    item = dict(row)
    item.pop('raw_bytes', None)
    metadata_json = item.pop('metadata_json', '{}')
    result_json = item.pop('import_result_json', '{}')
    item['metadata'] = json.loads(metadata_json or '{}')
    item['import_result'] = json.loads(result_json or '{}')
    return item


def list_datasets(root: Path, role: str | None = None) -> list[dict[str, Any]]:
    path = database_path(root)
    if not path.is_file():
        return []
    if role not in (None, 'train', 'test'):
        raise ValueError('工作区用途无效')
    with _connect(path) as db:
        rows = db.execute('''SELECT d.*,COUNT(r.task_id) AS active_refs
          FROM user_csv_datasets d LEFT JOIN user_csv_task_refs r ON r.dataset_id=d.id
          WHERE (? IS NULL OR d.role=?) GROUP BY d.id ORDER BY d.created_utc DESC''',
          (role, role)).fetchall()
    return [_public_record(row) for row in rows]


def get_dataset(root: Path, dataset_id: str, *, include_bytes: bool = False) -> dict[str, Any]:
    if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
        raise ValueError('数据集 ID 无效')
    path = database_path(root)
    if not path.is_file():
        raise KeyError('用户 CSV 不存在')
    with _connect(path) as db:
        row = db.execute('SELECT * FROM user_csv_datasets WHERE id=?', (dataset_id,)).fetchone()
    if row is None:
        raise KeyError('用户 CSV 不存在')
    item = dict(row)
    raw = item.pop('raw_bytes')
    metadata_json = item.pop('metadata_json')
    result_json = item.pop('import_result_json', '{}')
    item['metadata'] = json.loads(metadata_json or '{}')
    item['import_result'] = json.loads(result_json or '{}')
    if include_bytes:
        item['raw_bytes'] = raw
    return item


def set_import_result(root: Path, dataset_id: str, result: dict[str, Any], *, status: str = 'imported') -> None:
    if status not in ('uploaded', 'importing', 'imported', 'failed') or not isinstance(result, dict):
        raise ValueError('CSV 导入状态无效')
    path = database_path(root)
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''UPDATE user_csv_datasets SET source_id=?,import_status=?,import_result_json=?,
                      import_task_id=CASE WHEN ?='importing' THEN import_task_id ELSE NULL END,
                      import_started_utc=CASE WHEN ?='importing' THEN import_started_utc ELSE NULL END
                      WHERE id=?''',
            (result.get('source_id'), status,
             json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), status, status, dataset_id))
        if db.total_changes < 1:
            raise KeyError('用户 CSV 不存在')


def claim_import(root: Path, dataset_id: str, *, task_id: str | None,
                 status_lookup: Callable[[str], dict | None] | None = None) -> bool:
    """Atomically claim a pending/failed import; reject a concurrent active retry."""
    path = database_path(root)
    now = datetime.now(timezone.utc).isoformat()
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT import_status,import_task_id FROM user_csv_datasets WHERE id=?',
                         (dataset_id,)).fetchone()
        if row is None:
            raise KeyError('用户 CSV 不存在')
        if row['import_status'] == 'imported':
            return False
        if row['import_status'] == 'importing':
            prior_task = row['import_task_id']
            if not prior_task or not callable(status_lookup):
                raise RuntimeError('该 CSV 已有导入正在进行或状态无法确认，请稍后刷新任务状态')
            try:
                prior_status = status_lookup(prior_task)
            except Exception as exc:
                raise RuntimeError('无法确认上一次 CSV 导入任务状态，暂不允许重复提交') from exc
            if prior_status and prior_status.get('status') not in _TERMINAL_TASK_STATES:
                raise RuntimeError('该 CSV 已有导入任务正在运行，禁止重复提交')
            db.execute("UPDATE user_csv_datasets SET import_status='failed',import_task_id=NULL,import_started_utc=NULL WHERE id=?",
                       (dataset_id,))
        db.execute("UPDATE user_csv_datasets SET import_status='importing',import_task_id=?,import_started_utc=? WHERE id=?",
                   (task_id, now, dataset_id))
        return True


def reconcile_imports(root: Path, status_lookup: Callable[[str], dict | None]) -> int:
    """Turn interrupted imports into retryable failures without deleting raw CSV."""
    path = database_path(root)
    if not path.is_file():
        return 0
    with _connect(path) as db:
        rows = db.execute("SELECT id,import_task_id FROM user_csv_datasets WHERE import_status='importing'").fetchall()
    recovered = 0
    for row in rows:
        task_id = row['import_task_id']
        if not task_id:
            continue
        try:
            status = status_lookup(task_id)
        except Exception:
            continue
        if status is not None and status.get('status') in _TERMINAL_TASK_STATES:
            with _connect(path) as db:
                db.execute("UPDATE user_csv_datasets SET import_status='failed',import_task_id=NULL,import_started_utc=NULL WHERE id=? AND import_status='importing'",
                           (row['id'],))
            recovered += 1
    return recovered


def find_import_by_key(root: Path, import_key: str) -> dict[str, Any] | None:
    path = database_path(root)
    if not path.is_file():
        return None
    with _connect(path) as db:
        row = db.execute('SELECT * FROM user_csv_datasets WHERE import_key=?', (import_key,)).fetchone()
    return _public_record(row) if row else None


def bind_task(root: Path, task_id: str, dataset_id: str) -> None:
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise ValueError('任务 ID 无效')
    path = database_path(root)
    if not path.is_file():
        raise KeyError('用户 CSV 不存在')
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM user_csv_datasets WHERE id=? AND import_status='imported'", (dataset_id,)).fetchone():
            raise KeyError('用户 CSV 尚未完成导入或不存在')
        db.execute('INSERT OR IGNORE INTO user_csv_task_refs VALUES(?,?,?)',
          (task_id, dataset_id, datetime.now(timezone.utc).isoformat()))


def task_reference_ids(root: Path) -> list[str]:
    path = database_path(root)
    if not path.is_file():
        return []
    with _connect(path) as db:
        return [row[0] for row in db.execute('SELECT DISTINCT task_id FROM user_csv_task_refs')]


def release_task(root: Path, task_id: str) -> None:
    path = database_path(root)
    if not path.is_file():
        return
    with _connect(path) as db:
        db.execute('DELETE FROM user_csv_task_refs WHERE task_id=?', (task_id,))


def reconcile_task_refs(root: Path, status_lookup: Callable[[str], dict | None]) -> int:
    """Release references left by terminal/interrupted tasks after a restart."""
    released = 0
    for task_id in task_reference_ids(root):
        try:
            status = status_lookup(task_id)
        except Exception:
            # A transient task-history failure must fail closed: retain the
            # reference until the runtime can establish a terminal state.
            continue
        if status is not None and status.get('status') in _TERMINAL_TASK_STATES:
            release_task(root, task_id)
            released += 1
    return released


def _delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    """Delete one user-owned logical source and all dependent rows transactionally."""
    segment_ids = [row[0] for row in conn.execute('SELECT id FROM v2_segments WHERE source_id=?', (source_id,))]
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    # Delete all child rows whose declared FK points to this source or its segments.
    for table in tables:
        if table in {'v2_sources', 'v2_segments', 'v2_channels'}:
            continue
        foreign_keys = conn.execute('PRAGMA foreign_key_list("' + table.replace('"', '""') + '")').fetchall()
        for fk in foreign_keys:
            parent, child_col = fk[2], fk[3]
            quoted = '"' + table.replace('"', '""') + '"'
            if parent == 'v2_sources' and child_col == 'source_id':
                conn.execute(f'DELETE FROM {quoted} WHERE "{child_col}"=?', (source_id,))
            elif parent == 'v2_segments' and child_col in {'segment_id', 'input_segment_id', 'output_segment_id'} and segment_ids:
                marks = ','.join('?' for _ in segment_ids)
                conn.execute(f'DELETE FROM {quoted} WHERE "{child_col}" IN ({marks})', segment_ids)
    if segment_ids:
        marks = ','.join('?' for _ in segment_ids)
        conn.execute(f'DELETE FROM v2_segments WHERE id IN ({marks})', segment_ids)
        conn.execute('''DELETE FROM v2_channels WHERE id NOT IN
                        (SELECT DISTINCT channel_id FROM v2_segments WHERE channel_id IS NOT NULL)''')
    conn.execute('DELETE FROM v2_sources WHERE id=?', (source_id,))


def delete_dataset(root: Path, dataset_id: str, *, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError('删除用户 CSV 必须明确确认')
    if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
        raise ValueError('数据集 ID 无效')
    path = database_path(root)
    if not path.is_file():
        raise KeyError('用户 CSV 不存在')
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        refs = db.execute('SELECT task_id FROM user_csv_task_refs WHERE dataset_id=?', (dataset_id,)).fetchall()
        if refs:
            raise RuntimeError('该 CSV 正被任务引用，不能删除')
        row = db.execute('SELECT filename,sha256,source_id,import_status FROM user_csv_datasets WHERE id=?', (dataset_id,)).fetchone()
        if row is None:
            raise KeyError('用户 CSV 不存在')
        if row['import_status'] == 'importing':
            raise RuntimeError('该 CSV 正在导入，不能删除')
        if row['source_id'] is not None:
            _delete_source(db, int(row['source_id']))
        db.execute('DELETE FROM user_csv_datasets WHERE id=?', (dataset_id,))
    return {'id': dataset_id, 'filename': row['filename'], 'sha256': row['sha256'], 'deleted': True}
