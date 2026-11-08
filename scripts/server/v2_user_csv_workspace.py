"""Persistent user-owned CSV workspace isolated from the historical V2 database."""
from __future__ import annotations
import hashlib, json, re, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_NAME = "user_csv_workspace.sqlite"
_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_BYTES = 64 * 1024 * 1024

def database_path(root: Path) -> Path:
    return Path(root).resolve() / "datasets" / "user_csv_workspaces" / _DB_NAME

def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db

def initialize(root: Path) -> Path:
    path = database_path(root)
    with _connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS user_csv_datasets (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('train','test')),
          sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
          metadata_json TEXT NOT NULL, raw_bytes BLOB NOT NULL, created_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_user_csv_role_created
          ON user_csv_datasets(role, created_utc DESC);
        CREATE TABLE IF NOT EXISTS user_csv_task_refs (
          task_id TEXT NOT NULL,
          dataset_id TEXT NOT NULL REFERENCES user_csv_datasets(id) ON DELETE RESTRICT,
          created_utc TEXT NOT NULL, PRIMARY KEY(task_id,dataset_id)
        );
        """)
    return path

def import_csv(root: Path, raw: bytes, filename: str, role: str,
               metadata: dict[str, Any], *, format_confirmed: bool) -> dict[str, Any]:
    if format_confirmed is not True: raise ValueError("必须先完成 CSV 格式检查并明确确认")
    if role not in ("train","test"): raise ValueError("CSV 工作区用途只能是 train 或 test")
    if not isinstance(raw,bytes) or not raw or len(raw)>_MAX_BYTES:
        raise ValueError("CSV 文件为空或超过 64 MiB 限制")
    if not isinstance(filename,str) or not filename.strip() or len(filename)>255:
        raise ValueError("文件名无效")
    if not isinstance(metadata,dict): raise ValueError("CSV 检查摘要无效")
    dataset_id=uuid.uuid4().hex; digest=hashlib.sha256(raw).hexdigest()
    created=datetime.now(timezone.utc).isoformat(); path=initialize(root)
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO user_csv_datasets
          (id,filename,role,sha256,size_bytes,metadata_json,raw_bytes,created_utc)
          VALUES(?,?,?,?,?,?,?,?)""",
          (dataset_id,filename,role,digest,len(raw),
           json.dumps(metadata,ensure_ascii=False,sort_keys=True),raw,created))
    return {"id":dataset_id,"filename":filename,"role":role,"sha256":digest,
            "size_bytes":len(raw),"created_utc":created,"metadata":metadata}

def list_datasets(root: Path, role: str|None=None) -> list[dict[str,Any]]:
    path=database_path(root)
    if not path.is_file(): return []
    if role not in (None,"train","test"): raise ValueError("工作区用途无效")
    with _connect(path) as db:
        rows=db.execute("""SELECT d.id,d.filename,d.role,d.sha256,d.size_bytes,
          d.metadata_json,d.created_utc,COUNT(r.task_id) AS active_refs
          FROM user_csv_datasets d LEFT JOIN user_csv_task_refs r ON r.dataset_id=d.id
          WHERE (? IS NULL OR d.role=?) GROUP BY d.id ORDER BY d.created_utc DESC""",
          (role,role)).fetchall()
    return [{**dict(row),"metadata":json.loads(row["metadata_json"]),"metadata_json":None} for row in rows]

def get_dataset(root: Path, dataset_id: str, *, include_bytes: bool=False) -> dict[str,Any]:
    if not isinstance(dataset_id,str) or not _ID.fullmatch(dataset_id): raise ValueError("数据集 ID 无效")
    path=database_path(root)
    if not path.is_file(): raise KeyError("用户 CSV 不存在")
    with _connect(path) as db:
        row=db.execute("SELECT * FROM user_csv_datasets WHERE id=?",(dataset_id,)).fetchone()
    if row is None: raise KeyError("用户 CSV 不存在")
    item=dict(row); raw=item.pop("raw_bytes"); item["metadata"]=json.loads(item.pop("metadata_json"))
    if include_bytes: item["raw_bytes"]=raw
    return item

def bind_task(root: Path, task_id: str, dataset_id: str) -> None:
    if not isinstance(task_id,str) or not task_id or len(task_id)>128: raise ValueError("任务 ID 无效")
    path=database_path(root)
    if not path.is_file(): raise KeyError("用户 CSV 不存在")
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM user_csv_datasets WHERE id=?",(dataset_id,)).fetchone():
            raise KeyError("用户 CSV 不存在")
        db.execute("INSERT OR IGNORE INTO user_csv_task_refs VALUES(?,?,?)",
          (task_id,dataset_id,datetime.now(timezone.utc).isoformat()))

def release_task(root: Path, task_id: str) -> None:
    path=database_path(root)
    if not path.is_file(): return
    with _connect(path) as db: db.execute("DELETE FROM user_csv_task_refs WHERE task_id=?",(task_id,))

def delete_dataset(root: Path, dataset_id: str, *, confirm: bool) -> dict[str,Any]:
    if confirm is not True: raise ValueError("删除用户 CSV 必须明确确认")
    if not isinstance(dataset_id,str) or not _ID.fullmatch(dataset_id): raise ValueError("数据集 ID 无效")
    path=database_path(root)
    if not path.is_file(): raise KeyError("用户 CSV 不存在")
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        refs=db.execute("SELECT task_id FROM user_csv_task_refs WHERE dataset_id=?",(dataset_id,)).fetchall()
        if refs: raise RuntimeError("该 CSV 正被任务引用，不能删除")
        row=db.execute("SELECT filename,sha256 FROM user_csv_datasets WHERE id=?",(dataset_id,)).fetchone()
        if row is None: raise KeyError("用户 CSV 不存在")
        db.execute("DELETE FROM user_csv_datasets WHERE id=?",(dataset_id,))
    return {"id":dataset_id,"filename":row["filename"],"sha256":row["sha256"],"deleted":True}
