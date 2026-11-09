"""Shared SQLite table metadata and editable import-recognition rules.

The catalog and ingestion depend on this module; neither imports the other.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403
# ------------------------------------------------------------------
# 数据表业务用途（训练集 / 测试集）
# ------------------------------------------------------------------

_TABLE_META_NAME = "aas_table_meta"
_ROLE_LABELS = {
    "train": "训练集",
    "test": "测试集",
    "unspecified": "未指定",
}



def _quote_identifier(value: str) -> str:
    text = str(value or "")
    if not text or "\x00" in text:
        raise ValueError("identifier must be non-empty and contain no NUL byte")
    return '"' + text.replace('"', '""') + '"'


def _ensure_table_meta(conn: sqlite3.Connection) -> None:
    """Create the internal per-table business metadata registry when needed."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_META_NAME} (
            table_name TEXT PRIMARY KEY,
            declared_role TEXT NOT NULL DEFAULT 'auto',
            role_source TEXT NOT NULL DEFAULT 'auto',
            source_file TEXT DEFAULT '',
            imported_at TEXT NOT NULL
        )
        """
    )


def _reset_data_import_recognition_cache() -> None:
    """Clear the editable import-recognition rule cache.

    Normal operation does not require calling this function: the loader also
    detects changes by file modification time and size.  It mainly exists for
    tests and future administration UI hooks.
    """
    global _DATA_IMPORT_RECOGNITION_CACHE_KEY, _DATA_IMPORT_RECOGNITION_CACHE_VALUE  # pylint: disable=global-statement
    _DATA_IMPORT_RECOGNITION_CACHE_KEY = None
    _DATA_IMPORT_RECOGNITION_CACHE_VALUE = None


def _normalize_label_token(value: Any) -> str:
    return str(value).strip().lower()


def _load_data_import_recognition_config(*, strict: bool = False) -> dict[str, Any]:
    """Load editable column/label recognition rules from YAML.

    The file is reloaded automatically after it changes on disk, so adding an
    alias does not require restarting the AAS service. Missing sections fall
    back to the built-in defaults. Invalid configuration is surfaced during
    import preview/normalization when ``strict=True``; background table scans
    fall back safely to defaults instead of breaking data browsing.
    """
    global _DATA_IMPORT_RECOGNITION_CACHE_KEY, _DATA_IMPORT_RECOGNITION_CACHE_VALUE  # pylint: disable=global-statement

    path = Path(_service_setting("DATA_IMPORT_ALIASES_PATH"))
    try:
        stat = path.stat()
        cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        cache_key = (str(path.resolve()), None, None)

    if cache_key == _DATA_IMPORT_RECOGNITION_CACHE_KEY and _DATA_IMPORT_RECOGNITION_CACHE_VALUE is not None:
        cached = _DATA_IMPORT_RECOGNITION_CACHE_VALUE
        if strict and cached.get("config_error"):
            raise ValueError(cached["config_error"])
        return cached

    config_error = ""
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ValueError("配置文件根节点必须是 YAML 对象")
            raw = loaded
        except Exception as exc:  # pylint: disable=broad-exception-caught
            config_error = f"自动识别规则配置无效：{path}: {exc}"
            raw = {}

    columns_raw = raw.get("columns", {}) if isinstance(raw, dict) else {}
    labels_raw = raw.get("labels", {}) if isinstance(raw, dict) else {}
    if columns_raw is None:
        columns_raw = {}
    if labels_raw is None:
        labels_raw = {}
    if not isinstance(columns_raw, dict):
        config_error = config_error or "自动识别规则配置 columns 必须是对象"
        columns_raw = {}
    if not isinstance(labels_raw, dict):
        config_error = config_error or "自动识别规则配置 labels 必须是对象"
        labels_raw = {}

    columns: dict[str, set[str]] = {}
    for field, defaults in _DEFAULT_DATA_IMPORT_RECOGNITION["columns"].items():
        configured = columns_raw.get(field, defaults)
        if not isinstance(configured, list):
            config_error = config_error or f"自动识别规则 columns.{field} 必须是列表"
            configured = defaults
        aliases = {
            _normalized_column_name(item)
            for item in configured
            if str(item).strip()
        }
        columns[field] = {item for item in aliases if item}

    labels: dict[str, set[str]] = {}
    for kind, defaults in _DEFAULT_DATA_IMPORT_RECOGNITION["labels"].items():
        configured = labels_raw.get(kind, defaults)
        if not isinstance(configured, list):
            config_error = config_error or f"自动识别规则 labels.{kind} 必须是列表"
            configured = defaults
        labels[kind] = {
            _normalize_label_token(item)
            for item in configured
            if str(item).strip()
        }

    overlap = labels["normal"] & labels["fault"]
    if overlap:
        config_error = config_error or (
            "正常/故障标签配置存在重复值：" + ", ".join(sorted(overlap))
        )
        labels = {
            kind: {_normalize_label_token(item) for item in defaults}
            for kind, defaults in _DEFAULT_DATA_IMPORT_RECOGNITION["labels"].items()
        }

    try:
        display_path = str(path.resolve().relative_to(_service_setting("PROJECT_ROOT").resolve()))
    except (ValueError, OSError):
        display_path = str(path)

    result = {
        "columns": columns,
        "labels": labels,
        "config_path": display_path,
        "source": "yaml" if path.exists() and raw and not config_error else "builtin-default",
        "config_error": config_error,
    }
    _DATA_IMPORT_RECOGNITION_CACHE_KEY = cache_key
    _DATA_IMPORT_RECOGNITION_CACHE_VALUE = result

    if config_error:
        if strict:
            raise ValueError(config_error)
        logger.warning("%s；使用可用默认规则继续处理", config_error)
    return result


def _coerce_binary_label(
    value: Any,
    recognition_config: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Normalize configured normal/fault labels to the AAS 0/1 contract."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1, 0.0, 1.0):
        return int(value)
    text = _normalize_label_token(value)
    if text == "":
        return None
    rules = recognition_config or _load_data_import_recognition_config(strict=False)
    if text in rules["labels"]["normal"]:
        return 0
    if text in rules["labels"]["fault"]:
        return 1
    return None


def _label_counts_from_rows(
    rows: list[dict[str, Any]],
    label_key: str = "label",
    recognition_config: Optional[dict[str, Any]] = None,
) -> dict[str, int]:
    normal = fault = unknown = 0
    rules = recognition_config or _load_data_import_recognition_config(strict=False)
    for row in rows:
        if label_key not in row:
            unknown += 1
            continue
        label = _coerce_binary_label(row.get(label_key), rules)
        if label == 0:
            normal += 1
        elif label == 1:
            fault += 1
        else:
            unknown += 1
    return {"normal": normal, "fault": fault, "unknown": unknown}


def _infer_role_from_counts(normal: int, fault: int, *, has_label: bool) -> str:
    if has_label and normal > 0 and fault == 0:
        return "train"
    if has_label and normal > 0 and fault > 0:
        return "test"
    return "unspecified"


def _infer_role_from_name(table_name: str) -> str:
    import re

    lower = str(table_name or "").lower()
    if re.search(r"(?:^|_)train(?:_|$)", lower):
        return "train"
    if re.search(r"(?:^|_)test(?:_|$)", lower):
        return "test"
    return "unspecified"


def _table_role_info(
    conn: sqlite3.Connection,
    table_name: str,
    columns: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Return a table's effective business role and label composition."""
    qtable = _quote_identifier(table_name)
    columns = columns or [row[1] for row in conn.execute(f"PRAGMA table_info({qtable})").fetchall()]
    has_label = "label" in columns
    normal = fault = unknown = 0
    if has_label:
        try:
            grouped = conn.execute(f"SELECT label, COUNT(*) FROM {qtable} GROUP BY label").fetchall()
            for raw_label, count in grouped:
                label = _coerce_binary_label(raw_label)
                if label == 0:
                    normal += int(count)
                elif label == 1:
                    fault += int(count)
                else:
                    unknown += int(count)
        except sqlite3.DatabaseError:
            pass

    declared_role = None
    source_file = ""
    try:
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE_META_NAME,),
        ).fetchone()
        if meta_exists:
            row = conn.execute(
                f"SELECT declared_role, role_source, source_file FROM {_TABLE_META_NAME} WHERE table_name=?",
                (table_name,),
            ).fetchone()
            if row:
                declared_role = str(row[0] or "auto").strip().lower()
                source_file = str(row[2] or "")
    except sqlite3.DatabaseError:
        declared_role = None

    inferred_role = _infer_role_from_counts(normal, fault, has_label=has_label)
    if inferred_role == "unspecified":
        inferred_role = _infer_role_from_name(table_name)

    if declared_role in {"train", "test", "unspecified"}:
        effective_role = declared_role
        role_source = "declared"
    else:
        effective_role = inferred_role
        role_source = "inferred"

    if effective_role == "train":
        role_valid = bool(has_label and normal > 0 and fault == 0 and unknown == 0)
    elif effective_role == "test":
        role_valid = bool(has_label and normal > 0 and fault > 0 and unknown == 0)
    else:
        role_valid = True

    labeled_total = normal + fault
    return {
        "data_role": effective_role,
        "role_label": _ROLE_LABELS.get(effective_role, "未指定"),
        "role_source": role_source,
        "declared_role": declared_role or "auto",
        "inferred_role": inferred_role,
        "role_valid": role_valid,
        "normal_count": normal,
        "fault_count": fault,
        "unknown_label_count": unknown,
        "fault_rate": (fault / labeled_total) if labeled_total else 0.0,
        "source_file": source_file,
    }


def _save_table_role(
    conn: sqlite3.Connection,
    table_name: str,
    declared_role: str,
    *,
    source_file: str = "",
) -> None:
    role = str(declared_role or "auto").strip().lower()
    if role not in {"auto", "train", "test", "unspecified"}:
        raise ValueError("data_role must be auto/train/test/unspecified")
    _ensure_table_meta(conn)
    conn.execute(
        f"""
        INSERT INTO {_TABLE_META_NAME}(table_name, declared_role, role_source, source_file, imported_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            declared_role=excluded.declared_role,
            role_source=excluded.role_source,
            source_file=excluded.source_file,
            imported_at=excluded.imported_at
        """,
        (
            table_name,
            role,
            "declared" if role != "auto" else "auto",
            str(source_file or ""),
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _normalized_column_name(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", str(name or "").strip().lower()).strip("_")



__all__ = [name for name in globals() if not name.startswith("__") and name != "_service_setting"]
