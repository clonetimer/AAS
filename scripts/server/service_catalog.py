"""Phase 34: cohesive domain services, split from the original services.py.

Import through ``scripts.server.services`` for compatibility, or use this module
directly for new code. No database schema or API change is introduced.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403 - central paths and SQLite guard
from .service_models import read_json, _training_artifact_metadata, get_model_by_experiment_id
from .service_metadata import _quote_identifier, _table_role_info, _TABLE_META_NAME


# ------------------------------------------------------------------
# 数据管理服务
# ------------------------------------------------------------------

def list_databases() -> list[dict[str, Any]]:
    """列出所有可用数据库"""
    databases = []
    for db_path in sorted((_service_setting("PROJECT_ROOT") / "datasets").glob("*.db")):
        try:
            tables = sqlite_tables(db_path)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # 即使连接失败也添加到列表，但标记为错误
            databases.append({
                "label": db_path.name,
                "value": db_path.relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
                "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2),
                "table_count": 0,
                "preferred_table": None,
                "error": str(e),
            })
            continue

        # 即使没有表也添加到列表
        preferred_table = next(
            (item["name"] for item in tables if item["name"] == "history_data"),
            tables[0]["name"] if tables else None
        )

        # 检查是否存在包含 model 或 subsystem 字段的表
        has_required_fields = False
        for table in tables:
            if "model" in table.get("columns", []) or "subsystem" in table.get("columns", []):
                has_required_fields = True
                break

        databases.append({
            "label": db_path.name,
            "value": db_path.relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
            "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2),
            "table_count": len(tables),
            "preferred_table": preferred_table,
            "has_required_fields": has_required_fields,
        })

    # 按是否有必需字段和名称排序
    databases.sort(key=lambda item: (not item.get("has_required_fields", False), item["label"]))
    return databases


def list_experiment_configs() -> list[dict[str, str]]:
    """列出实验配置文件"""
    configs = []
    for config_path in sorted((_service_setting("PROJECT_ROOT") / "configs" / "experiments").glob("*.yaml")):
        configs.append({
            "label": config_path.name,
            "value": config_path.relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
        })
    return configs


def list_experiment_summaries(experiment_type: Optional[str] = None) -> list[dict[str, Any]]:
    """列出实验摘要"""
    summaries = []

    for summary_path in sorted((_service_setting("PROJECT_ROOT") / "outputs").glob("*/reports/summary.json")):
        try:
            summary = read_json(summary_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue

        if not isinstance(summary, dict):
            continue

        exp_info = summary.get("experiment", {})
        if not isinstance(exp_info, dict) or not exp_info:
            exp_info = {
                "experiment_id": summary.get("experiment_id", summary_path.parent.parent.name),
                "type": "evaluation",
                "run_dir": str(summary_path.parent.parent),
            }
        exp_type = exp_info.get("type", "evaluation")

        if experiment_type and exp_type != experiment_type:
            continue

        results = summary.get("results", [])
        detectors = [item.get("detector", "") for item in results if isinstance(item, dict)]
        success_count = sum(
            1
            for item in results
            if isinstance(item, dict)
            and (
                item.get("status") == "success"
                or (isinstance(item.get("raw_result"), dict) and item["raw_result"].get("status") == "success")
            )
        )
        metric_digest = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            metrics = item.get("metrics", {})
            if isinstance(metrics, dict):
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        metric_digest.setdefault(key, []).append(float(value))
        metric_avg = {k: round(sum(v) / len(v), 6) for k, v in metric_digest.items() if v}

        summary_record = {
            "experiment_id": exp_info.get("experiment_id", summary_path.parent.parent.name),
            "run_dir": exp_info.get("run_dir", str(summary_path.parent.parent)),
            "detectors": detectors,
            "detector_count": len(detectors),
            "success_count": success_count,
            "result_count": len(results),
            "metrics_avg": metric_avg,
            "path": summary_path.relative_to(_service_setting("PROJECT_ROOT")).as_posix(),
            "type": exp_type,
        }

        if exp_type == "training":
            training_info = summary.get("training", {})
            summary_record["algorithm"] = training_info.get("algorithm")
            summary_record["dataset"] = training_info.get("dataset")
            summary_record["table"] = training_info.get("table")
            summary_record["model_dir"] = training_info.get("model_dir")
            summary_record["model_file_path"] = training_info.get("model_file_path")

            model_dir, artifact = _training_artifact_metadata(training_info)
            artifact_exp_id = str(artifact.get("experiment_id") or "").strip() if artifact else ""
            registry_model = get_model_by_experiment_id(artifact_exp_id) if artifact_exp_id else None
            summary_record["model_available"] = bool(model_dir and artifact)
            summary_record["model_artifact_experiment_id"] = artifact_exp_id or None
            # 注册表是“是否已发布”的单一真相。历史 summary 中的布尔值可能因
            # model.db 被恢复/删除而过期；只在无法识别训练产物 ID 时兼容旧标记。
            summary_record["model_published"] = bool(registry_model) if artifact_exp_id else bool(training_info.get("model_published", False))
            summary_record["model_name"] = (registry_model or {}).get("model_name") if artifact_exp_id else training_info.get("model_name")
            summary_record["model_id"] = (registry_model or {}).get("id") if artifact_exp_id else training_info.get("model_id")
        elif exp_type == "evaluation":
            eval_info = summary.get("evaluation", {})
            summary_record["model_id"] = eval_info.get("model_id")
            summary_record["model_name"] = eval_info.get("model_name")
            summary_record["model_algorithm"] = eval_info.get("model_algorithm")
            summary_record["eval_dataset"] = eval_info.get("eval_dataset")
            summary_record["eval_table"] = eval_info.get("eval_table")
            summary_record["evaluation_mode"] = eval_info.get("evaluation_mode", "supervised")
            summary_record["ground_truth_available"] = eval_info.get("ground_truth_available", True)

        summaries.append(summary_record)

    summaries.sort(key=lambda item: item["experiment_id"], reverse=True)
    return summaries


def get_experiment_details(relative_summary_path: str) -> dict[str, Any]:
    """获取实验详情（仅允许 outputs/ 下的 summary.json）。"""
    summary_path = resolve_managed_file(
        _service_setting("PROJECT_ROOT") / "outputs",
        relative_summary_path,
        anchor=_service_setting("PROJECT_ROOT"),
        required_name="summary.json",
        must_exist=True,
    )
    payload = read_json(summary_path)
    experiment = payload.get("experiment", {}) if isinstance(payload, dict) else {}
    training = payload.get("training", {}) if isinstance(payload, dict) else {}
    evaluation = payload.get("evaluation", {}) if isinstance(payload, dict) else {}
    results = payload.get("results", []) if isinstance(payload, dict) else []
    rows = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_result = item.get("raw_result", {})
        metrics = item.get("metrics", {})
        rows.append(
            {
                "detector": item.get("detector"),
                "status": raw_result.get("status") if raw_result else item.get("status"),
                "error_code": raw_result.get("error_code") if raw_result else item.get("error_code"),
                "metrics": metrics,
                "chart_trace": item.get("chart_trace") if isinstance(item.get("chart_trace"), dict) else None,
            }
        )
    return {"experiment": experiment, "training": training, "evaluation": evaluation,
            "provenance": payload.get("provenance") if isinstance(payload, dict) else None,
            "rows": rows}


def sqlite_tables(
    db_path: Path,
    model: Optional[str] = None,
    subsystem: Optional[str] = None,
    device: Optional[str] = None,
    variable: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List compact and legacy single-channel tables via one canonical identity."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = []
        for name in names:
            if not name or name.lower().startswith('sqlite_') or name.startswith('aas_') or name.lower().endswith('_clean'):
                continue
            physical = set(catalog.columns(conn, name))
            identity = catalog.signature(conn, name)
            if identity:
                hierarchy = [identity]
            elif set(catalog.IDENTITY).issubset(physical):
                # Older mixed-channel telemetry is browsable but cannot be
                # migrated or treated as a multivariate training source.
                qname = _quote_identifier(name)
                hierarchy = [dict(zip(catalog.IDENTITY, map(str, row))) for row in
                    conn.execute(f'SELECT DISTINCT model, subsystem, device, variable FROM {qname}')]
            else:
                continue
            matching = [row for row in hierarchy if all(
                not expected or row[key] == expected for key, expected in (
                    ('model', model), ('subsystem', subsystem), ('device', device), ('variable', variable)
                ))]
            if not matching:
                continue
            fields = catalog.logical_columns(conn, name)
            qtable = _quote_identifier(name)
            count = conn.execute(f'SELECT COUNT(*) FROM {qtable}').fetchone()[0]
            # Catalogue summaries are independent of a client's preview LIMIT.
            # No interpolation or synthetic sample clock is introduced here.
            if 'timestamp' in physical:
                bounds = conn.execute(f'SELECT MIN("timestamp"), MAX("timestamp") FROM {qtable}').fetchone()
                start_time, end_time = bounds[0], bounds[1]
            else:
                start_time = end_time = None
            role_info = _table_role_info(conn, name, fields)
            # Compatibility-wide tables remain browsable/exportable, but are not
            # ordinary one-measurement training or test sources. Extra metadata
            # fields are allowed; additional sensor channels are not.
            is_single = (len(hierarchy) == 1 and
                {'timestamp', 'value', 'label'}.issubset(physical) and
                {field.lower() for field in physical}.issubset(
                    catalog.SAMPLE_FIELDS | set(catalog.IDENTITY)))
            result.append({'hierarchy': matching, 'name': name, 'columns': fields,
                           'row_count': count, 'is_single_measurement': is_single,
                           'time_start': start_time, 'time_end': end_time,
                           **role_info})
        return result


def delete_history_table(db_path: Path, table_name: str) -> dict[str, Any]:
    """Delete one browsable user data table and its derived clean table atomically.

    Reject internal SQLite/metadata tables and objects not exposed by the
    historical data browser. Managed db paths are resolved at the API boundary.
    """
    if not isinstance(table_name, str) or not table_name or "\x00" in table_name:
        raise ValueError("必须指定合法的数据表名")
    if table_name.lower().startswith("sqlite_") or table_name.startswith("aas_") or table_name.lower().endswith("_clean"):
        raise ValueError("不允许删除 SQLite 系统表、元数据表或独立清洁表")
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        if not exists:
            raise ValueError(f"数据表不存在：{table_name}")
        qtable = _quote_identifier(table_name)
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({qtable})")}
        if not (catalog.signature(conn, table_name) or set(catalog.IDENTITY).issubset(columns)):
            raise ValueError("只允许删除历史数据浏览中可见的业务数据表")

        clean_name = f"{table_name}_clean"
        clean_exists = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (clean_name,)
        ).fetchone())
        meta_exists = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE_META_NAME,)
        ).fetchone())
        with conn:  # DROP and registry cleanup form one SQLite transaction.
            from scripts.core.fault_intervals import delete_source_intervals
            delete_source_intervals(conn, table_name)
            delete_source_intervals(conn, clean_name)
            if clean_exists:
                conn.execute(f"DROP TABLE {_quote_identifier(clean_name)}")
            conn.execute(f"DROP TABLE {qtable}")
            if meta_exists:
                conn.execute(f"DELETE FROM {_quote_identifier(_TABLE_META_NAME)} WHERE table_name IN (?, ?)",
                             (table_name, clean_name))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (catalog.CATALOG,)).fetchone():
                conn.execute(f"DELETE FROM {catalog.CATALOG} WHERE table_name IN (?, ?)", (table_name, clean_name))
                catalog.prune_unused(conn)
    return {"status": "success", "table_name": table_name, "deleted_clean_table": clean_exists}


def sqlite_rows(
    db_path: Path,
    table_name: str,
    limit: int = 200,
    offset: int = 0,
    time_col: str | None = None,
    start_time: Any | None = None,
    end_time: Any | None = None,
) -> dict[str, Any]:
    """获取表的行数据，可选按指定时间列/时间段过滤。

    时间过滤在 SQLite 侧完成，而不是只对前端已加载的前 N 行做筛选，
    因而能够准确查看大表中任意指定时间段。所有表名/列名都先经过真实
    schema 校验，再使用标识符引用，避免把客户端参数直接拼进 SQL。
    """
    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if table_name not in tables or table_name.startswith('aas_'):
            raise ValueError(f"table does not exist: {table_name}")

        qtable = _quote_identifier(table_name)
        columns = catalog.logical_columns(conn, table_name)
        total_count = int(conn.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()[0])

        where: list[str] = []
        params: list[Any] = []
        order_sql = ""
        if time_col:
            if time_col not in columns:
                raise ValueError(f"time column does not exist: {time_col}")
            qtime = _quote_identifier(time_col)
            if start_time is not None and str(start_time) != "":
                where.append(f"{qtime} >= ?")
                params.append(start_time)
            if end_time is not None and str(end_time) != "":
                where.append(f"{qtime} <= ?")
                params.append(end_time)
            order_sql = f" ORDER BY {qtime} ASC"

        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        matched_count = int(
            conn.execute(f"SELECT COUNT(*) FROM {qtable}{where_sql}", params).fetchone()[0]
        )
        identity = catalog.signature(conn, table_name) if catalog.is_compact(conn, table_name) else None
        rows = [
            catalog.expand_known(dict(row), identity, columns) if identity else dict(row)
            for row in conn.execute(
                f"SELECT * FROM {qtable}{where_sql}{order_sql} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        ]
    return {
        "columns": columns,
        "rows": rows,
        "total_count": total_count,
        "matched_count": matched_count,
        "truncated": offset + len(rows) < matched_count,
    }


def sqlite_time_range(
    db_path: Path,
    table_name: str,
    time_col: str,
) -> dict[str, Any]:
    """返回当前表指定时间列的完整首尾范围和类型提示。"""
    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if table_name not in tables:
            raise ValueError(f"table does not exist: {table_name}")

        qtable = _quote_identifier(table_name)
        schema = conn.execute(f"PRAGMA table_info({qtable})").fetchall()
        columns = [row[1] for row in schema]
        if time_col not in columns:
            raise ValueError(f"time column does not exist: {time_col}")
        qtime = _quote_identifier(time_col)

        row = conn.execute(
            f"SELECT MIN({qtime}) AS min_value, MAX({qtime}) AS max_value, "
            f"COUNT({qtime}) AS non_null_count, COUNT(*) AS total_count FROM {qtable}"
        ).fetchone()
        storage_types = {
            str(item[0])
            for item in conn.execute(
                f"SELECT typeof({qtime}), COUNT(*) FROM {qtable} "
                f"WHERE {qtime} IS NOT NULL GROUP BY typeof({qtime})"
            ).fetchall()
        }
        declared_type = next((str(item[2] or "") for item in schema if item[1] == time_col), "")

    if storage_types and storage_types.issubset({"integer", "real"}):
        value_type = "number"
    else:
        value_type = "text"
    return {
        "time_col": time_col,
        "min": row["min_value"],
        "max": row["max_value"],
        "non_null_count": int(row["non_null_count"] or 0),
        "total_count": int(row["total_count"] or 0),
        "declared_type": declared_type,
        "value_type": value_type,
    }


def sqlite_continuous_series(
    db_path: Path, identity: dict[str, str], *, start_time: Any = None,
    end_time: Any = None, limit: int = 400, offset: int = 0,
    role: str | None = None,
) -> dict[str, Any]:
    """Read a logical channel, optionally enforcing train/test role isolation."""
    from scripts.server.continuous_series import query_continuous_series
    selected = None
    if role is not None:
        from scripts.server.logical_selection import normalize_identity, select_role_tables
        identity = normalize_identity(identity)
        selected = select_role_tables(sqlite_tables(db_path), identity, role)
    return query_continuous_series(db_path, identity, start_time=start_time,
        end_time=end_time, limit=limit, offset=offset, included_tables=selected,
        simulation_db_path=_service_setting("SIMULATION_DB_PATH"))


def sqlite_dataset_meta(db_path: Path) -> dict[str, str]:
    """获取数据集元信息"""
    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        rows = conn.execute("SELECT key, value FROM dataset_meta").fetchall()
    return {str(key): str(value) for key, value in rows}


def sqlite_device_index(db_path: Path) -> dict[str, Any]:
    """Return device index information for both supported SQLite layouts.

    Older generated datasets contain ``device_table_index`` while the current
    history/simulation datasets expose ``table_index``.  The API keeps one
    stable response shape and derives a device-level view when only
    ``table_index`` is present.
    """
    with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "device_table_index" in tables:
            columns = [
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(device_table_index)"
                ).fetchall()
            ]
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM device_table_index ORDER BY device"
                ).fetchall()
            ]
            return {"columns": columns, "rows": rows}

        if "table_index" in tables:
            columns = [
                "device",
                "table_name",
                "subsystem_count",
                "variable_count",
                "row_count",
                "description",
            ]
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        device,
                        GROUP_CONCAT(table_name) AS table_name,
                        COUNT(DISTINCT model || ':' || subsystem) AS subsystem_count,
                        COUNT(*) AS variable_count,
                        COALESCE(SUM(row_count), 0) AS row_count,
                        NULL AS description
                    FROM table_index
                    GROUP BY device
                    ORDER BY device
                    """
                ).fetchall()
            ]
            return {"columns": columns, "rows": rows}

    raise ValueError("database does not provide a supported device index table")


def sqlite_hierarchy_options(
    db_path: Path,
    model: Optional[str] = None,
    subsystem: Optional[str] = None,
    device: Optional[str] = None,
) -> dict[str, Any]:
    """Cascade hierarchy filters from registered identity or compatible legacy rows."""
    records = sqlite_tables(db_path)
    selected = [row for item in records for row in item['hierarchy']
                if (model is None or row['model'] == model)
                and (subsystem is None or row['subsystem'] == subsystem)
                and (device is None or row['device'] == device)]
    return {label: sorted({entry[key] for entry in selected if entry[key]})
            for label, key in [('models', 'model'), ('subsystems', 'subsystem'),
                               ('devices', 'device'), ('variables', 'variable')]}




__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_service_setting"}]
