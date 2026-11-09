"""Legacy CSV preview/import transaction engine.

Depends on metadata and shared managed-path contracts, not on HTTP routes.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403
from .service_metadata import (
    _quote_identifier, _coerce_binary_label, _infer_role_from_counts,
    _label_counts_from_rows, _load_data_import_recognition_config,
    _save_table_role, _table_role_info, _normalized_column_name,
    _ROLE_LABELS, _TABLE_META_NAME,
)
def _parse_import_rows(file_content: str, file_type: str) -> list[dict[str, Any]]:
    import csv
    from io import StringIO

    normalized_type = str(file_type or "").strip().lower()
    if normalized_type == "csv":
        rows = list(csv.DictReader(StringIO(file_content)))
    elif normalized_type == "json":
        data = json.loads(file_content)
        rows = data if isinstance(data, list) else [data]
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Imported rows must be non-empty objects")
    return rows


def _find_alias(columns: list[str], aliases: set[str]) -> Optional[str]:
    normalized = {col: _normalized_column_name(col) for col in columns}
    # Exact normalized match first.
    for col, key in normalized.items():
        if key in aliases:
            return col
    # Then allow a conservative suffix match for namespaced fields such as
    # label.fault_active -> fault_active.
    for col, key in normalized.items():
        if any(key.endswith("_" + alias) for alias in aliases):
            return col
    return None


def _sample_is_numeric(rows: list[dict[str, Any]], column: str) -> bool:
    seen = numeric = 0
    for row in rows[:50]:
        raw = row.get(column)
        if raw in (None, ""):
            continue
        seen += 1
        try:
            float(raw)
            numeric += 1
        except (TypeError, ValueError):
            pass
    return bool(seen and numeric / seen >= 0.8)


def _dataset_tag(filename: str, role: str) -> str:
    lower = str(filename or "").lower()
    if role == "train":
        return "train"
    if "开路" in filename or "open" in lower or "circuit" in lower:
        return "test_open_circuit"
    if any(token in filename for token in ("容量突降", "容量损失")) or "capacity_loss" in lower or "capacity" in lower:
        return "test_capacity_loss"
    if role == "test":
        return "test"
    return "data"


def _suggest_import_mapping(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    filename: str = "",
) -> dict[str, Any]:
    recognition = _load_data_import_recognition_config(strict=True)
    aliases = recognition["columns"]

    direct = {
        "timestamp": _find_alias(columns, aliases["timestamp"]),
        "label": _find_alias(columns, aliases["label"]),
        "model": _find_alias(columns, aliases["model"]),
        "subsystem": _find_alias(columns, aliases["subsystem"]),
        "device": _find_alias(columns, aliases["device"]),
        "variable": _find_alias(columns, aliases["variable"]),
        "value": _find_alias(columns, aliases["value"]),
    }

    telemetry_columns: list[dict[str, Any]] = []
    for col in columns:
        if col == direct.get("label") or col == direct.get("timestamp"):
            continue
        if str(col).lower().startswith("label."):
            continue
        parts = [part.strip() for part in str(col).split(".") if part.strip()]
        if len(parts) < 3 or not _sample_is_numeric(rows, col):
            continue
        if len(parts) == 3:
            model = ""
            subsystem, device, variable = parts
        else:
            model, subsystem, device = parts[0], parts[1], parts[2]
            variable = "_".join(parts[3:])
        telemetry_columns.append(
            {
                "column": col,
                "model": model,
                "subsystem": subsystem,
                "device": device,
                "variable": variable,
            }
        )

    primary: dict[str, Any] | None = None
    if telemetry_columns:
        primary = telemetry_columns[0]
        if not direct["value"]:
            direct["value"] = primary["column"]

    mappings: dict[str, dict[str, str]] = {}
    for field in ("timestamp", "model", "subsystem", "device", "variable", "value", "label"):
        source = direct.get(field)
        if source:
            mappings[field] = {"type": "column", "value": source, "source": "auto"}

    # A dotted telemetry name carries useful hierarchy information. Prefer it
    # over generic target_name/parameter columns because it maps one value
    # column to one AAS business table unambiguously.
    if primary:
        if primary.get("model") and not direct.get("model"):
            mappings["model"] = {"type": "manual", "value": primary["model"], "source": "telemetry"}
        mappings["subsystem"] = {"type": "manual", "value": primary["subsystem"], "source": "telemetry"}
        mappings["device"] = {"type": "manual", "value": primary["device"], "source": "telemetry"}
        mappings["variable"] = {"type": "manual", "value": primary["variable"], "source": "telemetry"}
        mappings["value"] = {"type": "column", "value": primary["column"], "source": "telemetry"}

    label_col = direct.get("label")
    counts = {"normal": 0, "fault": 0, "unknown": 0}
    if label_col:
        counts = _label_counts_from_rows(
            [{"label": row.get(label_col)} for row in rows],
            recognition_config=recognition,
        )
    suggested_role = _infer_role_from_counts(
        counts["normal"], counts["fault"], has_label=bool(label_col)
    )
    if suggested_role == "unspecified":
        lower_name = str(filename or "").lower()
        if "训练" in filename or "train" in lower_name:
            suggested_role = "train"
        elif "测试" in filename or "test" in lower_name:
            suggested_role = "test"

    return {
        "suggested_mappings": mappings,
        "telemetry_columns": telemetry_columns,
        "suggested_role": suggested_role,
        "role_label": _ROLE_LABELS.get(suggested_role, "未指定"),
        "label_summary": counts,
        "dataset_tag": _dataset_tag(filename, suggested_role),
        "auto_mapped_fields": sorted(mappings.keys()),
        "recognition_config": {
            "path": recognition["config_path"],
            "source": recognition["source"],
        },
    }


def preview_import(file_content: str, file_type: str, filename: str = "") -> dict[str, Any]:
    """Preview imported data and suggest field/role mappings.

    Suggestions intentionally remain editable in the browser. The service only
    auto-recognizes conservative, common aliases and dotted telemetry columns;
    it never silently invents a model hierarchy that is absent from the file.
    """
    rows = _parse_import_rows(file_content, file_type)
    columns = list(rows[0].keys()) if rows else []
    suggestions = _suggest_import_mapping(columns, rows, filename=filename)
    return {
        "columns": columns,
        "rows": rows[:100],
        "total_rows": len(rows),
        **suggestions,
    }


def _is_numeric_feature(value: Any) -> bool:
    if value is None or value == '':
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _apply_field_mappings(
    source_rows: list[dict[str, Any]],
    field_mappings: Optional[dict],
    *, preserve_unmapped_numeric: bool = True,
) -> list[dict[str, Any]]:
    if not field_mappings:
        rows = [dict(row) for row in source_rows]
    else:
        rows = []
        # Preserve unmapped numeric telemetry once, rather than re-scanning
        # the entire input for every row (which would be quadratic).
        consumed = {m.get('value') for m in field_mappings.values()
                    if isinstance(m, dict) and m.get('type') == 'column'}
        reserved = {'timestamp', 'label', 'model', 'subsystem', 'device', 'variable',
                    'value', 'id', 'fault_id', 'fault_description', 'fault_severity',
                    'affected_component', 'fault_log_text'}
        extra_candidates = [col for col in source_rows[0] if
                            isinstance(col, str) and col not in consumed and
                            col not in field_mappings and col.lower() not in reserved and
                            all(ch.isalnum() or ch == '_' for ch in col)] if source_rows else []
        extra_fields = [col for col in extra_candidates if
                        any(_is_numeric_feature(row.get(col)) for row in source_rows)] if preserve_unmapped_numeric else []
        for source_row in source_rows:
            new_row: dict[str, Any] = {}
            for field_name, mapping in field_mappings.items():
                if not isinstance(mapping, dict):
                    raise ValueError('field mapping must be an object')
                mapping_type = mapping.get('type')
                if mapping_type == 'column':
                    source_column = mapping.get('value')
                    if source_column not in source_row:
                        raise ValueError(f'映射列不存在：{source_column}')
                    new_row[field_name] = source_row.get(source_column)
                elif mapping_type == 'manual':
                    new_row[field_name] = mapping.get('value')
                else:
                    raise ValueError(f'unsupported field mapping type: {mapping_type}')
            for column in extra_fields:
                new_row[column] = source_row.get(column)
            rows.append(new_row)

    if rows and "label" in rows[0]:
        recognition = _load_data_import_recognition_config(strict=True)
        for index, row in enumerate(rows, start=1):
            raw = row.get("label")
            normalized = _coerce_binary_label(raw, recognition)
            if normalized is None and raw not in (None, ""):
                raise ValueError(f"第 {index} 行标签值无法识别为正常/故障：{raw!r}")
            row["label"] = normalized
    return rows


def _fill_missing_measurement_values(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Causal forward-fill for numeric ``value`` within each telemetry series.

    Never infer hierarchy, timestamps or fault labels, and never use future data.
    An unobserved leading value stays SQL NULL. Works for single-table and
    wide-table batch jobs because each job maps its own telemetry ``value``.
    """
    latest_by_series: dict[tuple[Any, ...], Any] = {}
    missing = filled = 0
    for row in rows:
        if "value" not in row:
            continue
        key = tuple(row.get(field) for field in ("model", "subsystem", "device", "variable"))
        value = row["value"]
        empty = value is None or (isinstance(value, str) and value.strip().lower() in {
            "", "na", "n/a", "nan", "null", "none", "inf", "-inf", "infinity", "-infinity",
        }) or (isinstance(value, (float, int)) and not math.isfinite(value))
        if empty:
            missing += 1
            # Incomplete metadata is not sufficient to establish series identity.
            if any(part is None or str(part).strip() == "" for part in key):
                row["value"] = None
                continue
            row["value"] = latest_by_series.get(key)
            if key in latest_by_series:
                filled += 1
            continue
        # Only previously observed finite numerical measurements may be used.
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(numeric) and all(part is not None and str(part).strip() for part in key):
            latest_by_series[key] = numeric
    return {"missing_values": missing, "filled_values": filled, "unfilled_values": missing - filled}


def _validate_import_role(rows: list[dict[str, Any]], requested_role: str) -> tuple[str, dict[str, int]]:
    role = str(requested_role or "auto").strip().lower()
    if role not in {"auto", "train", "test", "unspecified"}:
        raise ValueError("data_role must be auto/train/test/unspecified")
    has_label = bool(rows and "label" in rows[0])
    counts = _label_counts_from_rows(rows) if has_label else {"normal": 0, "fault": 0, "unknown": len(rows)}
    inferred = _infer_role_from_counts(counts["normal"], counts["fault"], has_label=has_label)

    if role == "train":
        if not has_label:
            raise ValueError("训练集必须提供 label 字段，以验证“仅正常数据”约束。")
        if counts["unknown"]:
            raise ValueError(f"训练集包含 {counts['unknown']} 条无法识别的标签。")
        if counts["fault"]:
            raise ValueError(f"训练集必须仅包含正常数据；当前检测到 {counts['fault']} 条故障样本。")
        if counts["normal"] == 0:
            raise ValueError("训练集没有可识别的正常样本。")
    elif role == "test":
        if not has_label:
            raise ValueError("测试集必须提供 label 字段，以验证“正常+故障”组成。")
        if counts["unknown"]:
            raise ValueError(f"测试集包含 {counts['unknown']} 条无法识别的标签。")
        if not (counts["normal"] > 0 and counts["fault"] > 0):
            raise ValueError(
                "测试集必须同时包含正常数据和故障数据；"
                f"当前正常 {counts['normal']} 条、故障 {counts['fault']} 条。"
            )

    effective = inferred if role == "auto" else role
    return effective, counts


def _import_rows_into_connection(
    conn: sqlite3.Connection,
    source_rows: list[dict[str, Any]],
    *,
    table_prefix: str,
    table_name: str,
    field_mappings: Optional[dict],
    data_role: str,
    source_file: str = "",
    fill_missing: bool = False,
    preserve_unmapped_numeric: bool = True,
) -> dict[str, Any]:
    import re

    if not isinstance(fill_missing, bool):
        raise ValueError("fill_missing must be boolean")
    rows = _apply_field_mappings(
        source_rows, field_mappings,
        preserve_unmapped_numeric=preserve_unmapped_numeric,
    )
    fill_stats = (_fill_missing_measurement_values(rows) if fill_missing else
                  {"missing_values": 0, "filled_values": 0, "unfilled_values": 0})
    if not rows:
        raise ValueError("No data to import")

    full_table_name = f"{table_prefix}_{table_name}" if table_prefix else str(table_name)
    if not re.fullmatch(r"[A-Za-z0-9_]+", full_table_name):
        raise ValueError("table_prefix and table_name may contain only letters, digits and underscore")

    required_business_fields = {"model", "subsystem", "device", "variable", "value"}
    if field_mappings:
        missing = sorted(required_business_fields - set(rows[0].keys()))
        if missing:
            raise ValueError("缺少数据浏览必需字段：" + ", ".join(missing))

    effective_role, counts = _validate_import_role(rows, data_role)

    columns = list(rows[0].keys())
    if not columns:
        raise ValueError("No columns to import")
    extra_columns = sorted({key for row in rows for key in row.keys()} - set(columns))
    if extra_columns:
        raise ValueError(f"Rows contain inconsistent columns: {', '.join(map(str, extra_columns))}")

    # A business table is a single sensor channel. Do not silently keep additional
    # numeric measurements under other names: those would bypass per-table role
    # assignment and leak into multivariate training in ambiguous order.
    non_measurement = {
        'id','timestamp','model','subsystem','device','variable','value','label',
        'created_at','fault_id','fault_description','fault_severity','fault_log_text',
        'affected_component','normal_start_time','normal_end_time',
        'source_file','source_name','source_row','data_role','quality_flag',
    }
    additional_measurements = [key for key in columns
        if str(key).lower() not in non_measurement
        and any(_is_numeric_feature(row.get(key)) for row in rows)]
    if additional_measurements:
        raise ValueError(
            '单表只能有一列测量值 value；检测到其他数值列：'
            + ', '.join(map(str, additional_measurements))
            + '。请改为批量按参量拆表后导入。'
        )

    # Store the channel identity once in a catalog, not on every sample row.
    identities = {tuple(str(row.get(key) or '').strip() for key in catalog.IDENTITY) for row in rows}
    if len(identities) != 1 or not all(next(iter(identities))):
        raise ValueError('单参量表的型号/分系统/部件/参量必须非空且在整表内一致；请按参量拆分后导入')
    identity = dict(zip(catalog.IDENTITY, next(iter(identities))))
    physical_columns = [col for col in columns if col not in catalog.IDENTITY]
    cursor = conn.cursor()
    qtable = _quote_identifier(full_table_name)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (full_table_name,)).fetchone()
    if exists:
        existing_columns = catalog.columns(conn, full_table_name)
        if existing_columns == columns:  # Existing legacy schema: append compatibly.
            old_identity = catalog.identity_from_legacy(conn, full_table_name)
            if old_identity and old_identity != identity:
                raise ValueError('目标表包含其他参量，不能混合写入')
            if not old_identity and conn.execute(f'SELECT COUNT(*) FROM {qtable}').fetchone()[0]:
                raise ValueError('无法确认旧表参量身份，拒绝追加')
            physical_columns = columns
        elif existing_columns != physical_columns or not catalog.is_compact(conn, full_table_name):
            raise ValueError(f'目标表 {full_table_name} 字段结构不同；请修改表名后重新导入')
        else:
            catalog.register(conn, full_table_name, identity, 'compact')
    else:
        column_defs = []
        for col in physical_columns:
            qcol = _quote_identifier(col)
            lower = str(col).lower()
            if lower in {'id', 'fault_id'}:
                column_defs.append(f'{qcol} INTEGER PRIMARY KEY AUTOINCREMENT')
            elif lower in {'timestamp', 'created_at', 'start_time', 'end_time'}:
                column_defs.append(f'{qcol} TEXT')
            elif lower == 'label':
                column_defs.append(f'{qcol} INTEGER CHECK({qcol} IN (0, 1))')
            elif lower == 'value' or any(_is_numeric_feature(row.get(col)) for row in rows):
                column_defs.append(f'{qcol} REAL')
            else:
                column_defs.append(f'{qcol} TEXT')
        cursor.execute(f'CREATE TABLE {qtable} ({", ".join(column_defs)})')
        catalog.register(conn, full_table_name, identity, 'compact')

    placeholders = ', '.join('?' for _ in physical_columns)
    qcolumns = ', '.join(_quote_identifier(col) for col in physical_columns)
    insert_sql = f'INSERT INTO {qtable} ({qcolumns}) VALUES ({placeholders})'
    for row in rows:
        cursor.execute(insert_sql, [row.get(col) for col in physical_columns])

    _save_table_role(conn, full_table_name, data_role, source_file=source_file)
    row_count = cursor.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()[0]
    role_info = _table_role_info(conn, full_table_name, columns)
    return {
        "status": "success",
        "table_name": full_table_name,
        "row_count": row_count,
        "imported_rows": len(rows),
        "requested_rows": len(rows),
        "requested_role": data_role,
        "effective_role": effective_role,
        "fill_missing": fill_missing,
        **fill_stats,
        "label_summary": counts,
        **role_info,
    }


def import_data_multi(
    file_content: str,
    file_type: str,
    target_db: str,
    jobs: list[dict[str, Any]],
    *,
    source_file: str = "",
    fill_missing: bool = False,
) -> dict[str, Any]:
    """Import one source file into one or more AAS business tables atomically.

    This is used by batch/wide-data import so a large CSV is uploaded and parsed
    only once even when several telemetry columns are expanded into separate
    parameter tables.
    """
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("jobs must be a non-empty list")
    if not isinstance(fill_missing, bool):
        raise ValueError("fill_missing must be boolean")
    source_rows = _parse_import_rows(file_content, file_type)
    db_path = _resolve_database_path(target_db)
    results: list[dict[str, Any]] = []
    try:
        with guarded_sqlite_connection(db_path, simulation_db_path=_service_setting("SIMULATION_DB_PATH")) as conn:
            for job in jobs:
                if not isinstance(job, dict):
                    raise ValueError("each import job must be an object")
                results.append(
                    _import_rows_into_connection(
                        conn,
                        source_rows,
                        table_prefix=(
                            str(job.get("table_prefix"))
                            if "table_prefix" in job and job.get("table_prefix") is not None
                            else "DF"
                        ),
                        table_name=str(job.get("table_name") or "imported_data"),
                        field_mappings=job.get("field_mappings") or {},
                        data_role=str(job.get("data_role") or "auto"),
                        source_file=source_file,
                        fill_missing=fill_missing,
                        preserve_unmapped_numeric=len(jobs) == 1,
                    )
                )
            conn.commit()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"data import failed: {exc}") from exc
    return {
        "status": "success",
        "source_file": source_file,
        "tables_created": len(results),
        "results": results,
        "imported_rows_total": sum(int(item.get("imported_rows") or 0) for item in results),
        "filled_values_total": sum(int(item.get("filled_values") or 0) for item in results),
        "unfilled_values_total": sum(int(item.get("unfilled_values") or 0) for item in results),
    }


def import_data(
    file_content: str,
    file_type: str,
    target_db: str,
    table_prefix: str,
    table_name: str,
    field_mappings: Optional[dict] = None,
    data_role: str = "auto",
    source_file: str = "",
    fill_missing: bool = False,
) -> dict[str, Any]:
    """Safely import one CSV/JSON mapping into a managed SQLite database."""
    result = import_data_multi(
        file_content,
        file_type,
        target_db,
        [
            {
                "table_prefix": table_prefix,
                "table_name": table_name,
                "field_mappings": field_mappings or {},
                "data_role": data_role,
            }
        ],
        source_file=source_file,
        fill_missing=fill_missing,
    )
    return result["results"][0]


__all__ = [name for name in globals() if not name.startswith("__") and name != "_service_setting"]
