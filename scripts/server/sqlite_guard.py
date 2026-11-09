from __future__ import annotations

import gc
import os
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path
from typing import Iterator

# All access to the managed simulation database within the AAS process shares
# this lock.  ThreadingHTTPServer can otherwise run read/write/delete handlers
# concurrently, which is especially problematic on Windows where an open SQLite
# handle prevents unlink/replace of the database file.
SIMULATION_DB_LOCK = threading.RLock()



def simulation_db_locked(func):
    """Serialize one whole simulation-database operation inside this process."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        with SIMULATION_DB_LOCK:
            return func(*args, **kwargs)
    return wrapper

def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_simulation_db(path: str | Path, simulation_db_path: str | Path | None) -> bool:
    if simulation_db_path is None:
        return False
    try:
        return _resolved(path) == _resolved(simulation_db_path)
    except (OSError, RuntimeError):
        return False


@contextmanager
def sqlite_connection(
    path: str | Path,
    *,
    simulation_db_path: str | Path | None = None,
    timeout: float = 10.0,
) -> Iterator[sqlite3.Connection]:
    """Open SQLite with deterministic close and optional simulation DB lock.

    ``sqlite3.Connection`` as a context manager commits/rolls back transactions
    but does *not* itself guarantee ``close()`` at the end of the ``with``
    block.  That is easy to miss and can leave a Windows file handle alive long
    enough for delete/replace to fail with WinError 32.  This helper always
    closes explicitly.
    """
    guard = SIMULATION_DB_LOCK if is_simulation_db(path, simulation_db_path) else nullcontext()
    with guard:
        conn = sqlite3.connect(str(path), timeout=max(0.1, float(timeout)))
        try:
            # Enforce normalized catalog foreign keys in managed DB transactions.
            conn.execute("PRAGMA foreign_keys=ON")
            # Keep SQLite-level lock contention bounded and predictable.
            conn.execute(f"PRAGMA busy_timeout={int(max(100, timeout * 1000))}")
            yield conn
        finally:
            try:
                conn.close()
            finally:
                # Defensive on Windows: release transient cursor/statement
                # references before a lifecycle operation attempts unlink.
                if os.name == "nt" and is_simulation_db(path, simulation_db_path):
                    gc.collect()


def _is_windows_sharing_violation(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in {32, 33}


def unlink_with_retry(path: str | Path, *, attempts: int = 8, delay: float = 0.12) -> None:
    """Delete a file, retrying transient Windows sharing violations.

    This cannot override a genuinely external long-lived lock (for example DB
    Browser for SQLite).  It is intended to absorb short-lived handles from
    in-flight AAS requests, antivirus/indexing, and delayed SQLite cleanup.
    """
    target = Path(path)
    last_exc: OSError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            target.unlink(missing_ok=True)
            return
        except OSError as exc:  # preserve original filesystem exception on final failure
            last_exc = exc
            if not (_is_windows_sharing_violation(exc) or isinstance(exc, PermissionError)):
                raise
            gc.collect()
            if attempt + 1 < attempts:
                time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def replace_with_retry(
    source: str | Path,
    destination: str | Path,
    *,
    attempts: int = 8,
    delay: float = 0.12,
) -> None:
    """Atomically replace a file with bounded retry on Windows sharing locks."""
    src = Path(source)
    dst = Path(destination)
    last_exc: OSError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            last_exc = exc
            if not (_is_windows_sharing_violation(exc) or isinstance(exc, PermissionError)):
                raise
            gc.collect()
            if attempt + 1 < attempts:
                time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def busy_message(action: str, exc: BaseException, path: str | Path) -> str:
    """Return an actionable Chinese message for Windows file-sharing failures."""
    if _is_windows_sharing_violation(exc) or isinstance(exc, PermissionError):
        return (
            f"{action}失败：模拟数据库正在被其他操作或进程占用，Windows 暂时不允许访问文件 "
            f"“{Path(path)}”。请先停止正在进行的预览/注入/评估，关闭其他 AAS 窗口以及 "
            "DB Browser、VS Code SQLite 扩展等可能打开该数据库的工具，等待数秒后重试。"
        )
    return f"{action}失败: {exc}"
