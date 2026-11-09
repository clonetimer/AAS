"""Common service contracts, managed paths and SQLite guard."""
from __future__ import annotations

import datetime
import json
import logging
import math
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 数据库路径常量
MODELS_DIR = PROJECT_ROOT / "models"
MODEL_DB_PATH = MODELS_DIR / "model.db"
SIMULATION_DB_PATH = PROJECT_ROOT / "datasets" / "simulation.db"
HISTORY_SAMPLE_DB_PATH = PROJECT_ROOT / "datasets" / "history_sample.db"
DATA_IMPORT_ALIASES_PATH = PROJECT_ROOT / "configs" / "data_import_aliases.yaml"

# Default recognition rules are kept in code as a safe fallback when the
# editable YAML file is missing.  When the file exists, each configured field
# replaces the corresponding default list so users can add/remove aliases
# without editing Python source.
_DEFAULT_DATA_IMPORT_RECOGNITION = {
    "columns": {
        "timestamp": [
            "timestamp", "time", "time_s", "sim_time", "sim_time_s",
            "simulation_time", "datetime", "date_time", "ts",
            "采样时间", "时间", "时间戳", "仿真时间",
        ],
        "label": [
            "label", "fault", "fault_active", "is_fault", "fault_label",
            "anomaly", "abnormal", "标签", "故障标签", "是否故障",
        ],
        "model": ["model", "model_name", "satellite_model", "型号", "卫星型号"],
        "subsystem": ["subsystem", "sub_system", "subsys", "分系统", "子系统"],
        "device": [
            "device", "component", "unit", "equipment", "target_name",
            "单机", "设备", "部件",
        ],
        "variable": [
            "variable", "parameter", "param", "channel", "signal",
            "参量", "参数", "测点",
        ],
        "value": [
            "value", "val", "reading", "measurement", "measure",
            "数值", "值", "测量值",
        ],
    },
    "labels": {
        "normal": [
            "0", "0.0", "false", "no", "normal", "nominal", "healthy",
            "ok", "正常", "无故障",
        ],
        "fault": [
            "1", "1.0", "true", "yes", "fault", "faulty", "abnormal",
            "anomaly", "故障", "异常",
        ],
    },
}

_DATA_IMPORT_RECOGNITION_CACHE_KEY = None
_DATA_IMPORT_RECOGNITION_CACHE_VALUE = None

# 确保项目根目录在 sys.path 中
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.path_policy import ManagedPathError, resolve_managed_file
from scripts.core.model_manager import validate_saved_model

from scripts.server import series_catalog as catalog

from scripts.server.sqlite_guard import (
    busy_message as simulation_busy_message,
    replace_with_retry as simulation_replace_with_retry,
    simulation_db_locked,
    sqlite_connection as guarded_sqlite_connection,
    unlink_with_retry as simulation_unlink_with_retry,
)


def _resolve_database_path(requested: str | Path, *, must_exist: bool = True) -> Path:
    raw = Path(str(requested or "").strip())
    logical = raw
    if str(raw) and not raw.is_absolute() and len(raw.parts) == 1:
        logical = Path("datasets") / raw
    return resolve_managed_file(
        PROJECT_ROOT / "datasets",
        logical,
        anchor=PROJECT_ROOT,
        allowed_suffixes=(".db", ".sqlite", ".sqlite3"),
        must_exist=must_exist,
    )


def _resolve_model_dir(requested: str | Path, *, must_exist: bool = True) -> Path:
    root = (PROJECT_ROOT / "models").resolve()
    raw = Path(str(requested or "").strip())
    if not str(raw):
        raise ManagedPathError("model directory is required")
    candidate = (raw if raw.is_absolute() else PROJECT_ROOT / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ManagedPathError("path must stay under managed root: models") from exc
    if candidate.is_file():
        candidate = candidate.parent
    if candidate == root:
        raise ManagedPathError("model directory must be a child of managed root: models")
    if must_exist and not candidate.is_dir():
        raise FileNotFoundError(f"model directory does not exist: {requested}")
    return candidate


# ------------------------------------------------------------------


def _service_setting(name: str):
    """Resolve runtime-adjustable paths via the legacy public entrypoint.

    Existing installations and tests may override paths in services.py.
    Centralize that compatibility here instead of mutating globals per request.
    """
    from scripts.server import services
    return getattr(services, name)

__all__ = [name for name in globals() if not name.startswith('__')]
