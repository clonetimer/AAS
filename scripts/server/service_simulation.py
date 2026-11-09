"""Phase 34: cohesive domain services, split from the original services.py.

Import through ``scripts.server.services`` for compatibility, or use this module
directly for new code. No database schema or API change is introduced.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403 - central paths and SQLite guard


# ------------------------------------------------------------------
# 仿真服务
# ------------------------------------------------------------------

def _remove_simulation_sidecars() -> None:
    """Remove SQLite WAL/SHM sidecars belonging to the managed simulation DB."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(_service_setting("SIMULATION_DB_PATH")) + suffix)
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


@simulation_db_locked
def _simulation_table_counts() -> tuple[int, int]:
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return (0, 0)
    try:
        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        tables = [t for t in tables if not t.startswith('aas_') and t not in
                  {'table_index', 'device_table_index', 'variable_catalog', 'dataset_meta', 'fault_records'}]
        fault = [t for t in tables if "_fault_" in t]
        original = [t for t in tables if "_fault_" not in t]
        return (len(original), len(fault))
    except (sqlite3.Error, OSError, ValueError):
        return (0, 0)


def _is_algorithm_split_table(table_name: str) -> bool:
    """Return whether a history table exists only for algorithm train/test splitting.

    These derivative tables are useful in the algorithm runner but should not
    become selectable source tables in simulation.db, where the user expects
    the original historical telemetry tables.
    """
    lower = str(table_name or "").lower()
    return lower.endswith("_train") or lower.endswith("_test_holdout")


@simulation_db_locked
def init_simulation_db(force: bool = False) -> dict[str, Any]:
    """Create simulation.db from history_sample.db, optionally replacing it atomically."""
    import shutil
    import uuid

    if not _service_setting("HISTORY_SAMPLE_DB_PATH").exists():
        return {"status": "error", "message": f"源数据库不存在: {_service_setting('HISTORY_SAMPLE_DB_PATH')}"}

    existed = _service_setting("SIMULATION_DB_PATH").exists()
    if existed and not force:
        original_count, fault_count = _simulation_table_counts()
        return {
            "status": "exists",
            "message": "模拟数据库已存在。可使用“更新模拟数据库”同步源数据，或使用“重新初始化模拟数据库”完全重建。",
            "original_tables": original_count,
            "fault_tables": fault_count,
        }

    original_before, fault_before = _simulation_table_counts() if existed else (0, 0)
    _service_setting("SIMULATION_DB_PATH").parent.mkdir(parents=True, exist_ok=True)
    temp_path = _service_setting("SIMULATION_DB_PATH").with_name(f".{_service_setting('SIMULATION_DB_PATH').name}.rebuild-{uuid.uuid4().hex}.tmp")

    try:
        shutil.copy2(_service_setting("HISTORY_SAMPLE_DB_PATH"), temp_path)
        with guarded_sqlite_connection(temp_path) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            clean_tables = [t for t in tables if t.endswith("_clean")]
            algorithm_split_tables = [t for t in tables if _is_algorithm_split_table(t)]
            internal_tables = [t for t in tables if t == "aas_table_meta"]
            for table_name in clean_tables + algorithm_split_tables + internal_tables:
                conn.execute(f'DROP TABLE IF EXISTS "{table_name.replace(chr(34), chr(34)*2)}"')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (catalog.CATALOG,)).fetchone():
                conn.execute(f"DELETE FROM {catalog.CATALOG} WHERE table_name NOT IN "
                             "(SELECT name FROM sqlite_master WHERE type='table')")
                catalog.prune_unused(conn)
            conn.commit()
            remaining = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        # Old WAL/SHM files must not survive a full replacement.  The old DB is
        # intentionally discarded only after the replacement image is complete.
        if existed:
            _remove_simulation_sidecars()
        simulation_replace_with_retry(temp_path, _service_setting("SIMULATION_DB_PATH"))
        _remove_simulation_sidecars()

        verb = "重新初始化" if existed else "初始化"
        message = f"模拟数据库{verb}成功，共 {len(remaining)} 张表"
        if existed and fault_before:
            message += f"；已清除原有 {fault_before} 张故障表"
        return {
            "status": "success",
            "message": message,
            "tables": remaining,
            "removed_clean_tables": clean_tables,
            "removed_algorithm_split_tables": algorithm_split_tables,
            "reinitialized": existed,
            "discarded_original_tables": original_before if existed else 0,
            "discarded_fault_tables": fault_before if existed else 0,
            "created_at": datetime.datetime.now().isoformat(),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        action = "重新初始化模拟数据库" if existed else "初始化模拟数据库"
        return {
            "status": "busy" if isinstance(exc, PermissionError) else "error",
            "message": simulation_busy_message(action, exc, _service_setting("SIMULATION_DB_PATH")),
        }


@simulation_db_locked
def delete_simulation_db() -> dict[str, Any]:
    """Delete the managed simulation database without touching history_sample.db."""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        _remove_simulation_sidecars()
        return {"status": "success", "message": "模拟数据库不存在，无需删除", "deleted": False}

    original_count, fault_count = _simulation_table_counts()
    try:
        with guarded_sqlite_connection(
            _service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH"), timeout=5.0
        ) as conn:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            except sqlite3.DatabaseError:
                pass

        simulation_unlink_with_retry(_service_setting("SIMULATION_DB_PATH"))
        _remove_simulation_sidecars()
        return {
            "status": "success",
            "message": f"模拟数据库已删除（原始表 {original_count} 张，故障表 {fault_count} 张），可重新初始化。",
            "deleted": True,
            "deleted_original_tables": original_count,
            "deleted_fault_tables": fault_count,
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "status": "busy" if isinstance(exc, PermissionError) else "error",
            "message": simulation_busy_message("删除模拟数据库", exc, _service_setting("SIMULATION_DB_PATH")),
            "deleted": False,
        }

@simulation_db_locked
def sync_simulation_db() -> dict[str, Any]:
    """从 history_sample.db 同步新表和新记录到 simulation.db"""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return {"status": "error", "message": "模拟数据库不存在，请先初始化"}

    if not _service_setting("HISTORY_SAMPLE_DB_PATH").exists():
        return {"status": "error", "message": f"源数据库不存在: {_service_setting('HISTORY_SAMPLE_DB_PATH')}"}

    try:
        with guarded_sqlite_connection(_service_setting("HISTORY_SAMPLE_DB_PATH")) as src_conn:
            src_tables = [row[0] for row in src_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            src_tables = [
                t for t in src_tables
                if not t.endswith("_clean")
                and not _is_algorithm_split_table(t)
                and not t.startswith('aas_')
                and t not in {'table_index', 'variable_catalog', 'device_table_index', 'dataset_meta', 'fault_records'}
            ]

        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as dst_conn:
            dst_tables = [row[0] for row in dst_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            fault_tables = [t for t in dst_tables if "_fault_" in t]
            dst_original_tables = [t for t in dst_tables if "_fault_" not in t]

        new_tables = [t for t in src_tables if t not in dst_original_tables]
        existing_tables = [t for t in src_tables if t in dst_original_tables]

        new_count = 0
        updated_count = 0

        with guarded_sqlite_connection(_service_setting("HISTORY_SAMPLE_DB_PATH")) as src_conn:
            with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as dst_conn:
                for table_name in new_tables:
                    schema = src_conn.execute(
                        f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"
                    ).fetchone()
                    if schema:
                        dst_conn.execute(schema[0])
                        src_conn.row_factory = sqlite3.Row
                        rows = [dict(row) for row in src_conn.execute(f"SELECT * FROM {table_name}").fetchall()]
                        if rows:
                            columns = list(rows[0].keys())
                            placeholders = ', '.join(['?'] * len(columns))
                            col_names = ', '.join(columns)
                            for row in rows:
                                values = [row.get(col) for col in columns]
                                dst_conn.execute(
                                    f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})", values
                                )
                        identity = catalog.signature(src_conn, table_name)
                        if identity and catalog.is_compact(src_conn, table_name):
                            catalog.register(dst_conn, table_name, identity, 'compact')
                        dst_conn.commit()
                        new_count += 1

                for table_name in existing_tables:
                    if set(catalog.columns(src_conn, table_name)) != set(catalog.columns(dst_conn, table_name)):
                        raise ValueError(f'{table_name} 的源/目标字段结构不同，请先备份并统一表结构后同步')
                    if catalog.is_compact(src_conn, table_name):
                        identity = catalog.signature(src_conn, table_name)
                        if identity is None:
                            raise ValueError(f'{table_name} 缺少目录身份')
                        catalog.register(dst_conn, table_name, identity, 'compact')
                    src_conn.row_factory = sqlite3.Row
                    src_all_rows = [dict(row) for row in src_conn.execute(f"SELECT * FROM {table_name}").fetchall()]

                    dst_conn.row_factory = sqlite3.Row
                    dst_all_rows = [dict(row) for row in dst_conn.execute(f"SELECT * FROM {table_name}").fetchall()]

                    if src_all_rows and "timestamp" in src_all_rows[0]:
                        src_rows = {row["timestamp"]: row for row in src_all_rows}
                        dst_rows = {row["timestamp"]: row for row in dst_all_rows}
                        new_keys = set(src_rows.keys()) - set(dst_rows.keys())
                        if new_keys:
                            columns = list(src_rows[next(iter(src_rows))].keys())
                            placeholders = ', '.join(['?'] * len(columns))
                            col_names = ', '.join(columns)
                            for ts in new_keys:
                                row = src_rows[ts]
                                values = [row.get(col) for col in columns]
                                dst_conn.execute(
                                    f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})", values
                                )
                            dst_conn.commit()
                            updated_count += 1
                    elif src_all_rows:
                        columns = list(src_all_rows[0].keys())
                        placeholders = ', '.join(['?'] * len(columns))
                        col_names = ', '.join(columns)
                        src_values_set = set(tuple(row[col] for col in columns) for row in src_all_rows)
                        dst_values_set = set(tuple(row[col] for col in columns) for row in dst_all_rows)
                        new_rows_values = src_values_set - dst_values_set
                        if new_rows_values:
                            for row_values in new_rows_values:
                                dst_conn.execute(
                                    f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})", row_values
                                )
                            dst_conn.commit()
                            updated_count += 1

        return {
            "status": "success",
            "message": f"同步完成：新增 {new_count} 张表，更新 {updated_count} 张表",
            "new_tables": new_tables,
            "updated_tables_count": updated_count,
            "fault_tables_preserved": fault_tables,
            "synced_at": datetime.datetime.now().isoformat(),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"status": "error", "message": f"同步失败: {e}"}


@simulation_db_locked
def get_simulation_status() -> dict[str, Any]:
    """获取模拟数据库状态"""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return {"exists": False, "message": "模拟数据库不存在"}

    try:
        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            tables = [t for t in tables if not t.startswith('aas_') and t not in
                      {'table_index', 'device_table_index', 'variable_catalog', 'dataset_meta', 'fault_records'}]
            fault_tables = [t for t in tables if "_fault_" in t]
            original_tables = [t for t in tables if "_fault_" not in t]

            total_rows = 0
            for t in original_tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                total_rows += count

        if _service_setting("HISTORY_SAMPLE_DB_PATH").exists():
            src_mtime = datetime.datetime.fromtimestamp(_service_setting("HISTORY_SAMPLE_DB_PATH").stat().st_mtime).isoformat()
        else:
            src_mtime = None

        return {
            "exists": True,
            "path": _service_setting("SIMULATION_DB_PATH").relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
            "original_tables": len(original_tables),
            "fault_tables": len(fault_tables),
            "fault_table_names": fault_tables,
            "total_rows": total_rows,
            "source_last_modified": src_mtime,
            "size_mb": round(_service_setting("SIMULATION_DB_PATH").stat().st_size / (1024 * 1024), 2),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"exists": False, "error": str(e)}


def get_simulation_fault_types() -> list[dict[str, Any]]:
    """Return built-in and user-installed fault types for the simulation UI."""
    from src.fault_plugins.registry import available_fault_types, discover_fault_types

    # Re-discover on request so descriptors copied into configs/fault_types while
    # the service is running also become visible without a restart.
    discover_fault_types()
    return available_fault_types()


@simulation_db_locked
def get_simulation_tables() -> list[dict[str, Any]]:
    """获取 simulation.db 中的表列表（排除故障表）"""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return []

    with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        result = []
        for table_name in tables:
            if "_fault_" in table_name or table_name.startswith('aas_') or table_name in ('table_index', 'variable_catalog', 'dataset_meta', 'device_table_index', 'fault_records'):
                continue
            columns = catalog.logical_columns(conn, table_name)
            count = conn.execute(f"SELECT COUNT(*) FROM {catalog.quote(table_name)}").fetchone()[0]
            result.append({"name": table_name, "columns": columns, "row_count": count})
    return result


@simulation_db_locked
def get_fault_tables() -> list[dict[str, Any]]:
    """获取 simulation.db 中的故障表列表"""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return []

    import re

    with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        result = []
        for table_name in tables:
            if "_fault_" not in table_name:
                continue
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

            match = re.match(r"(.+)_fault_(.+)_(\d{8}_\d{6})$", table_name)
            if match:
                original_table = match.group(1)
                fault_type = match.group(2)
                timestamp = match.group(3)
            else:
                original_table = table_name.split("_fault_")[0]
                fault_type = "unknown"
                timestamp = ""

            result.append({
                "name": table_name,
                "original_table": original_table,
                "fault_type": fault_type,
                "timestamp": timestamp,
                "columns": columns,
                "row_count": count,
                "size_kb": round(conn.execute(f"SELECT COUNT(*) * 0.1 FROM {table_name}").fetchone()[0], 1),
            })
    return result


@simulation_db_locked
def delete_fault_table(table_name: str) -> dict[str, Any]:
    """从 simulation.db 中删除故障表"""
    if not _service_setting("SIMULATION_DB_PATH").exists():
        return {"status": "error", "message": "模拟数据库不存在"}

    if "_fault_" not in table_name:
        return {"status": "error", "message": "只能删除故障表"}

    try:
        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if table_name not in tables:
                return {"status": "error", "message": f"表不存在: {table_name}"}

            conn.execute(f"DROP TABLE {catalog.quote(table_name)}")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (catalog.CATALOG,)).fetchone():
                conn.execute(f"DELETE FROM {catalog.CATALOG} WHERE table_name=?", (table_name,))
                catalog.prune_unused(conn)
            conn.commit()

        return {"status": "success", "message": f"已删除故障表: {table_name}"}
    except (sqlite3.Error, OSError) as e:
        return {"status": "error", "message": f"删除失败: {e}"}


def preview_fault_injection(
    db_path: str,
    table_name: str,
    fault_type: str,
    config: dict[str, Any],
    limit: int = 1000,
) -> dict[str, Any]:
    """安全预览故障注入效果。"""
    from src.fault_injection import create_injector

    try:
        full_db_path = _resolve_database_path(db_path)
    except (ManagedPathError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    try:
        with guarded_sqlite_connection(full_db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if table_name not in tables:
                return {"error": f"表不存在: {table_name}"}
            quoted = '"' + str(table_name).replace('"', '""') + '"'
            identity = catalog.signature(conn, table_name) if catalog.is_compact(conn, table_name) else None
            fields = catalog.logical_columns(conn, table_name) if identity else []
            rows = [
                catalog.expand_known(dict(row), identity, fields) if identity else dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {quoted} ORDER BY timestamp LIMIT ?",
                    (max(0, int(limit)),),
                ).fetchall()
            ]
        if not rows:
            return {"error": "表中没有数据"}
        original_data = deepcopy(rows)
        if fault_type == "__original__":
            return {
                "original": original_data,
                "faulty": [],
                "fault_type": fault_type,
                "view_mode": "original",
                "row_count": len(original_data),
            }

        from src.fault_plugins.registry import normalize_fault_config, validate_fault_output

        normalized_config = normalize_fault_config(fault_type, config)
        injector = create_injector(fault_type)
        fault_data = injector.inject(deepcopy(rows), normalized_config)
        fault_data = validate_fault_output(rows, fault_data)
        return {
            "original": original_data,
            "faulty": fault_data,
            "fault_type": fault_type,
            "row_count": len(original_data),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"error": f"预览失败: {exc}"}


@simulation_db_locked
def execute_fault_injection(
    table_name: str,
    fault_type: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """安全执行故障注入并创建故障表。"""
    from src.fault_injection import create_injector

    if not _service_setting("SIMULATION_DB_PATH").exists():
        return {"status": "error", "message": "模拟数据库不存在"}
    if fault_type == "__original__":
        return {"status": "error", "message": "原始曲线模式不执行故障注入；请选择一种故障类型"}
    try:
        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if table_name not in tables:
                return {"status": "error", "message": f"表不存在: {table_name}"}
            quoted = '"' + str(table_name).replace('"', '""') + '"'
            identity = catalog.signature(conn, table_name) if catalog.is_compact(conn, table_name) else None
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY timestamp").fetchall()]
        if not rows:
            return {"status": "error", "message": "表中没有数据"}
        from src.fault_plugins.registry import normalize_fault_config, validate_fault_output

        normalized_config = normalize_fault_config(fault_type, config)
        injector = create_injector(fault_type)
        fault_data = injector.inject(deepcopy(rows), normalized_config)
        fault_data = validate_fault_output(rows, fault_data)
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fault_table_name = f"{table_name}_fault_{fault_type}_{timestamp_str}"
        with guarded_sqlite_connection(_service_setting("SIMULATION_DB_PATH"), simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if not schema:
                return {"status": "error", "message": f"无法获取表 schema: {table_name}"}
            create_sql = schema[0].replace(table_name, fault_table_name, 1)
            conn.execute(create_sql)
            columns = list(fault_data[0].keys())
            placeholders = ", ".join("?" for _ in columns)
            qcols = ", ".join('"' + str(c).replace('"', '""') + '"' for c in columns)
            qfault = '"' + fault_table_name.replace('"', '""') + '"'
            insert_sql = f"INSERT INTO {qfault} ({qcols}) VALUES ({placeholders})"
            conn.executemany(insert_sql, [[row.get(col) for col in columns] for row in fault_data])
            if identity is not None:
                catalog.register(conn, fault_table_name, identity, 'compact')
            conn.commit()
        return {
            "status": "success",
            "message": f"故障注入完成，已创建表: {fault_table_name}",
            "fault_table_name": fault_table_name,
            "row_count": len(fault_data),
            "fault_type": fault_type,
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"status": "error", "message": f"注入失败: {exc}"}


__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_service_setting"}]
