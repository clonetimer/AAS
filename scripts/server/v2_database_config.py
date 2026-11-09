"""Admin-configured active V2 database, never from a browser request."""
from __future__ import annotations
import os
from pathlib import Path

DEFAULT_NAME = "aas_telemetry_v2.sqlite"

def active_name() -> str:
    name = os.environ.get("AAS_V2_DB_FILENAME", DEFAULT_NAME).strip()
    if not name or name in (".", "..") or Path(name).name != name or not name.lower().endswith((".sqlite", ".db")):
        raise ValueError("AAS_V2_DB_FILENAME 必须是 datasets 目录内的 .sqlite/.db 文件名，不能包含路径")
    return name

def active_path(root: Path) -> Path:
    return (Path(root).resolve() / "datasets" / active_name()).resolve()


def list_databases(root):
    """Enumerate safe local V2 files; do not expose arbitrary host paths."""
    import sqlite3
    directory=(Path(root).resolve()/'datasets')
    result=[]
    for entry in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not entry.is_file() or entry.is_symlink() or entry.suffix.lower() not in ('.sqlite','.db'): continue
        try:
            with sqlite3.connect(entry.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                version=db.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
                if version and str(version[0])=='2': result.append(entry.name)
        except sqlite3.DatabaseError:
            continue
    return result

def resolve_database(root,name=None):
    name=active_name() if not name else name
    if name != Path(name).name or not name.lower().endswith(('.sqlite','.db')):
        raise ValueError('数据库只能选择 datasets 目录内的 .sqlite 或 .db 文件')
    if name not in list_databases(root): raise ValueError('V2 数据库未安装、不可用或不是有效 V2 结构')
    return (Path(root).resolve()/'datasets'/name).resolve()
