"""Compatibility simulation database lifecycle helpers; V2 injection stays canonical.

Application runtime is passed explicitly to avoid implicit global state and cyclic imports.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

def _remove_simulation_sidecars(app) -> None:
    """Remove SQLite WAL/SHM sidecars belonging to the managed simulation DB."""
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(app.SIMULATION_DB_PATH) + suffix)
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass

def _simulation_table_counts(app) -> tuple[int, int]:
    if not app.SIMULATION_DB_PATH.exists():
        return (0, 0)
    try:
        with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        tables = [t for t in tables if not t.startswith('aas_') and t not in {'table_index', 'device_table_index', 'variable_catalog', 'dataset_meta', 'fault_records'}]
        fault = [t for t in tables if '_fault_' in t]
        original = [t for t in tables if '_fault_' not in t]
        return (len(original), len(fault))
    except (sqlite3.Error, OSError, ValueError):
        return (0, 0)

def _is_algorithm_split_table(app, table_name: str) -> bool:
    """Return whether a history table exists only for algorithm train/test splitting.

    These derivative tables are useful in the algorithm runner but should not
    become selectable source tables in simulation.db, where the user expects
    the original historical telemetry tables.
    """
    lower = str(table_name or '').lower()
    return lower.endswith('_train') or lower.endswith('_test_holdout')

def init_simulation_db(app, force: bool=False) -> dict[str, Any]:
    """Compatibility helper, NEVER copy a source database or erase V2 records."""
    if force:
        return {'status': 'error', 'message': 'V2 模拟数据库禁止整库重新初始化；请管理独立注入记录'}
    from scripts.server.v2_simulation_injection import seed_default
    return seed_default(app.ROOT)

def delete_simulation_db(app) -> dict[str, Any]:
    """Delete the managed simulation database without touching history_sample.db.

    Windows does not allow unlinking an SQLite file while any process still has
    an open handle.  All in-process simulation DB access is serialized by the
    shared guard and connections are explicitly closed before deletion.  A
    bounded retry absorbs short-lived Windows/antivirus handles; a genuinely
    external lock is reported with actionable guidance instead of raw WinError 32.
    """
    if not app.SIMULATION_DB_PATH.exists():
        app._remove_simulation_sidecars()
        return {'status': 'success', 'message': '模拟数据库不存在，无需删除', 'deleted': False}
    original_count, fault_count = app._simulation_table_counts()
    try:
        with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH, timeout=5.0) as conn:
            try:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
            except sqlite3.DatabaseError:
                pass
        app.simulation_unlink_with_retry(app.SIMULATION_DB_PATH)
        app._remove_simulation_sidecars()
        return {'status': 'success', 'message': f'模拟数据库已删除（原始表 {original_count} 张，故障表 {fault_count} 张），可重新初始化。', 'deleted': True, 'deleted_original_tables': original_count, 'deleted_fault_tables': fault_count}
    except Exception as exc:
        return {'status': 'busy' if isinstance(exc, PermissionError) else 'error', 'message': app.simulation_busy_message('删除模拟数据库', exc, app.SIMULATION_DB_PATH), 'deleted': False}

def sync_simulation_db(app) -> dict[str, Any]:
    """Old entire-database sync deliberately retired (no source copies)."""
    return {'status': 'retired', 'message': '不复制来源数据库；请使用 V2 按需故障注入'}

def get_simulation_status(app) -> dict[str, Any]:
    """Get simulation database status."""
    import datetime
    if not app.SIMULATION_DB_PATH.exists():
        return {'exists': False, 'message': '模拟数据库不存在'}
    try:
        with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            tables = [t for t in tables if not t.startswith('aas_') and t not in {'table_index', 'device_table_index', 'variable_catalog', 'dataset_meta', 'fault_records'}]
            fault_tables = [t for t in tables if '_fault_' in t]
            original_tables = [t for t in tables if '_fault_' not in t]
            total_rows = 0
            for t in original_tables:
                count = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                total_rows += count
        if app.HISTORY_SAMPLE_DB_PATH.exists():
            src_mtime = datetime.datetime.fromtimestamp(app.HISTORY_SAMPLE_DB_PATH.stat().st_mtime).isoformat()
        else:
            src_mtime = None
        return {'exists': True, 'path': app.SIMULATION_DB_PATH.relative_to(app.ROOT).as_posix(), 'original_tables': len(original_tables), 'fault_tables': len(fault_tables), 'fault_table_names': fault_tables, 'total_rows': total_rows, 'source_last_modified': src_mtime, 'size_mb': round(app.SIMULATION_DB_PATH.stat().st_size / (1024 * 1024), 2)}
    except Exception as e:
        return {'exists': False, 'error': str(e)}

def get_simulation_fault_types(app) -> list[dict[str, Any]]:
    """Return built-in and user-installed fault types for the simulation UI."""
    from src.fault_plugins.registry import available_fault_types, discover_fault_types
    discover_fault_types()
    return available_fault_types()

def get_simulation_tables(app) -> list[dict[str, Any]]:
    """Get table list from simulation.db (excluding fault tables)."""
    if not app.SIMULATION_DB_PATH.exists():
        return []
    with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        result = []
        for table_name in tables:
            if '_fault_' in table_name or table_name.startswith('aas_') or table_name in ('table_index', 'variable_catalog', 'dataset_meta', 'device_table_index', 'fault_records'):
                continue
            quoted_table = '"' + str(table_name).replace('"', '""') + '"'
            columns = app.series_catalog.logical_columns(conn, table_name)
            count = conn.execute(f'SELECT COUNT(*) FROM {quoted_table}').fetchone()[0]
            start_time = end_time = None
            if 'timestamp' in columns and count:
                bounds = conn.execute(f'SELECT MIN(timestamp), MAX(timestamp) FROM {quoted_table}').fetchone()
                if bounds:
                    start_time, end_time = (bounds[0], bounds[1])
            result.append({'name': table_name, 'columns': columns, 'row_count': count, 'start_time': start_time, 'end_time': end_time})
    return result

def get_fault_tables(app) -> list[dict[str, Any]]:
    """Get fault table list from simulation.db."""
    if not app.SIMULATION_DB_PATH.exists():
        return []
    import re
    with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        result = []
        for table_name in tables:
            if '_fault_' not in table_name:
                continue
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()]
            count = conn.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]
            match = re.match('(.+)_fault_(.+)_(\\d{8}_\\d{6})$', table_name)
            if match:
                original_table = match.group(1)
                fault_type = match.group(2)
                timestamp = match.group(3)
            else:
                original_table = table_name.split('_fault_')[0]
                fault_type = 'unknown'
                timestamp = ''
            result.append({'name': table_name, 'original_table': original_table, 'fault_type': fault_type, 'timestamp': timestamp, 'columns': columns, 'row_count': count, 'size_kb': round(conn.execute(f'SELECT COUNT(*) * 0.1 FROM {table_name}').fetchone()[0], 1)})
    return result

def delete_fault_table(app, table_name: str) -> dict[str, Any]:
    """Delete a fault table from simulation.db."""
    if not app.SIMULATION_DB_PATH.exists():
        return {'status': 'error', 'message': '模拟数据库不存在'}
    if '_fault_' not in table_name:
        return {'status': 'error', 'message': '只能删除故障表'}
    try:
        with app.guarded_sqlite_connection(app.SIMULATION_DB_PATH, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if table_name not in tables:
                return {'status': 'error', 'message': f'表不存在: {table_name}'}
            from scripts.core.fault_intervals import delete_source_intervals
            delete_source_intervals(conn, table_name)
            conn.execute(f'DROP TABLE {table_name}')
            conn.commit()
        return {'status': 'success', 'message': f'已删除故障表: {table_name}'}
    except (sqlite3.Error, OSError) as e:
        return {'status': 'error', 'message': f'删除失败: {e}'}

def preview_fault_injection(app, db_path: str, table_name: str, fault_type: str, config: dict[str, Any], limit: int=1000) -> dict[str, Any]:
    """Compatibility wrapper for the canonical simulation preview service.

    Keep the HTTP handler and direct service calls on the same implementation.
    In particular, the special ``__original__`` view mode is handled by
    ``scripts.server.services.preview_fault_injection`` before fault-plugin
    lookup, so it must never be sent to the fault registry as a plugin name.
    """
    from scripts.server.services import preview_fault_injection as service_preview_fault_injection
    return service_preview_fault_injection(db_path, table_name, fault_type, config, limit=limit)

def execute_fault_injection(app, table_name: str, fault_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the canonical simulation injection service."""
    from scripts.server.services import execute_fault_injection as service_execute_fault_injection
    return service_execute_fault_injection(table_name, fault_type, config)
