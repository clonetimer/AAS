"""Legacy physical-table preprocessing adapters (not the V2 quality pipeline).

Application context is an explicitly passed module/object. Database path overrides
and the SQLite guard are resolved at call time, avoiding stale shared state.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any

def check_clean_table_status(app, db_path: Path, table_name: str) -> dict[str, Any]:
    """检查清洁表的状态（存在/有效/失效）。"""
    clean_table_name = f"{table_name}_clean"

    if not db_path.exists():
        return {"status": "error", "message": f"数据库不存在：{db_path}"}

    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        # 检查原表是否存在
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if table_name not in tables:
            return {"status": "error", "message": f"原始表不存在：{table_name}"}

        # 检查清洁表是否存在
        if clean_table_name not in tables:
            return {"status": "not_exist", "clean_table": clean_table_name}

        # Quoted, schema-checked identifiers must never be interpolated raw.
        q_source = '"' + table_name.replace('"', '""') + '"'
        q_clean = '"' + clean_table_name.replace('"', '""') + '"'
        # 检查清洁表是否失效
        try:
            # 获取原表的快照
            original_meta = conn.execute(
                f"SELECT MAX(timestamp) as max_ts, COUNT(*) as count FROM {q_source}"
            ).fetchone()
            original_max_ts = original_meta[0] if original_meta[0] else ""
            original_count = original_meta[1] if original_meta[1] else 0

            # 获取清洁表的快照
            clean_meta = conn.execute(
                f"SELECT MAX(timestamp) as max_ts, COUNT(*) as count FROM {q_clean}"
            ).fetchone()
            clean_max_ts = clean_meta[0] if clean_meta[0] else ""
            clean_count = clean_meta[1] if clean_meta[1] else 0

            # 判断是否失效
            if original_max_ts != clean_max_ts or original_count != clean_count:
                return {
                    "status": "invalidated",
                    "clean_table": clean_table_name,
                    "original_max_ts": original_max_ts,
                    "original_count": original_count,
                    "clean_max_ts": clean_max_ts,
                    "clean_count": clean_count,
                    "message": "清洁表已失效（源数据已变更）"
                }

            return {
                "status": "valid",
                "clean_table": clean_table_name,
                "row_count": clean_count,
                "timestamp": clean_max_ts,
                "message": "清洁表有效"
            }
        except Exception as e:  # pylint: disable=broad-exception-caught
            return {"status": "error", "message": f"检查失败：{e}"}



def run_data_preprocessing(app, 
    db_path: Path,
    table_name: str,
    pipeline_config: dict[str, Any]
) -> dict[str, Any]:
    """执行数据预处理管线。"""
    import datetime

    clean_table_name = f"{table_name}_clean"

    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        # 读取原始数据
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if table_name not in tables:
            raise ValueError(f"原始表不存在：{table_name}")

        conn.row_factory = sqlite3.Row
        identity = app.series_catalog.signature(conn, table_name) if app.series_catalog.is_compact(conn, table_name) else None
        fields = app.series_catalog.logical_columns(conn, table_name) if identity else []
        rows = [app.series_catalog.expand_known(dict(row), identity, fields) if identity else dict(row)
                for row in conn.execute(f"SELECT * FROM {app.series_catalog.quote(table_name)} ORDER BY timestamp").fetchall()]

        if not rows:
            raise ValueError(f"原始表为空：{table_name}")

    # 执行预处理算子插件管线（与 PreprocessingManager 共享单一真相）
    from scripts.core.preproc_plugins.registry import DEFAULT_PIPELINE, get_operator, run_pipeline

    original_count = len(rows)

    # 内置管线保持稳定顺序；自定义算子仅在前端/调用方显式选择后追加，避免
    # “装了插件就悄悄改变所有数据”的不可控行为。
    plugin_steps = pipeline_config.get("plugin_steps", []) or []
    if not isinstance(plugin_steps, list):
        raise ValueError("pipeline_config.plugin_steps 必须是列表")
    step_names = list(DEFAULT_PIPELINE)
    for name in plugin_steps:
        name = str(name).strip()
        if name and name not in step_names and get_operator(name) is not None:
            step_names.append(name)

    rows = run_pipeline(step_names, rows, pipeline_config)
    steps_log = [
        {"step": name, "status": "success"}
        for name in step_names
        if get_operator(name) is not None and get_operator(name).enabled(pipeline_config)
    ]

    # 写入清洁表
    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        app.series_catalog.replace_derived_table(conn, table_name, clean_table_name, rows)

        conn.commit()

    app.console_log(f"[预处理] [OK] 完成，已创建清洁表：{clean_table_name}")

    return {
        "status": "success",
        "clean_table": clean_table_name,
        "rows_processed": original_count,
        "steps": steps_log,
        "completed_at": datetime.datetime.now().isoformat()
    }



def delete_clean_table(app, db_path: Path, table_name: str) -> dict[str, Any]:
    """删除清洁表。"""
    clean_table_name = f"{table_name}_clean"

    if not db_path.exists():
        return {"status": "error", "message": f"数据库不存在：{db_path}"}

    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if clean_table_name not in tables:
            return {"status": "not_exist", "message": f"清洁表不存在：{clean_table_name}"}

        conn.execute(f"DROP TABLE {app.series_catalog.quote(clean_table_name)}")
        if app.series_catalog.get(conn, clean_table_name):
            conn.execute(f"DELETE FROM {app.series_catalog.CATALOG} WHERE table_name=?", (clean_table_name,))
            app.series_catalog.prune_unused(conn)
        conn.commit()

    return {"status": "success", "message": f"已删除清洁表：{clean_table_name}"}

