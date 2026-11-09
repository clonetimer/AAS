"""Fault-type registry and YAML auto-discovery."""
from __future__ import annotations

from copy import deepcopy
from importlib.util import find_spec
from pathlib import Path
import re
from typing import Any, Optional

import yaml

from core.plugin_loader import import_plugin_class
from src.fault_injection import (
    FaultInjector,
    SpikeFaultInjector,
    DriftFaultInjector,
    BreakFaultInjector,
    PeriodicFaultInjector,
    NoiseFaultInjector,
)
from .base import BaseFaultType


FAULT_TYPE_REGISTRY: dict[str, FaultInjector] = {}
_DISCOVERED_CUSTOM_NAMES: set[str] = set()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs" / "fault_types"
FAULT_TYPE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

_BUILTIN_METADATA: dict[str, dict[str, Any]] = {
    "spike": {
        "label": "突刺",
        "description": "在目标区间内按原值比例瞬时抬升。",
        "formula": "y′ = y + s · |y|",
        "uses_severity": True,
        "supports_time_range": True,
        "config_schema": [],
    },
    "drift": {
        "label": "漂移",
        "description": "随区间进度逐渐偏离，可选择线性或指数漂移。",
        "formula": "线性：y′ = y + s · t · |y|；指数：y′ = y + s · (e^t - 1) · |y|",
        "uses_severity": True,
        "supports_time_range": True,
        "config_schema": [
            {
                "name": "drift_type", "label": "漂移类型", "type": "select",
                "default": "linear",
                "options": [
                    {"value": "linear", "label": "线性漂移"},
                    {"value": "exponential", "label": "指数漂移"},
                ],
            }
        ],
    },
    "break": {
        "label": "断点",
        "description": "将目标区间的 value 置为空值。",
        "formula": "y′ = NULL",
        "uses_severity": False,
        "supports_time_range": True,
        "config_schema": [],
    },
    "periodic": {
        "label": "周期",
        "description": "在目标区间叠加正弦周期扰动。",
        "formula": "y′ = y + s · sin(2π f t) · |y|",
        "uses_severity": True,
        "supports_time_range": True,
        "config_schema": [
            {
                "name": "frequency", "label": "区间内周期数 f", "type": "float",
                "default": 1.0, "min": 0.1, "step": 0.1,
                "description": "表示在所选区间内完成的周期数。",
            }
        ],
    },
    "noise": {
        "label": "噪声",
        "description": "在目标区间叠加零均值高斯噪声。",
        "formula": "y′ = y + N(0, s) · |y|",
        "uses_severity": True,
        "supports_time_range": True,
        "config_schema": [],
    },
}

_BUILTIN_CLASSES: dict[str, type[FaultInjector]] = {
    "spike": SpikeFaultInjector,
    "drift": DriftFaultInjector,
    "break": BreakFaultInjector,
    "periodic": PeriodicFaultInjector,
    "noise": NoiseFaultInjector,
}

_METADATA_FIELDS = (
    "name", "label", "description", "formula", "dependencies",
    "uses_severity", "supports_time_range", "config_schema",
)


def _apply_metadata(obj: FaultInjector, name: str, meta: dict[str, Any]) -> FaultInjector:
    setattr(obj, "name", name)
    setattr(obj, "label", meta.get("label", name))
    setattr(obj, "description", meta.get("description", ""))
    setattr(obj, "formula", meta.get("formula", ""))
    setattr(obj, "dependencies", list(meta.get("dependencies", []) or []))
    setattr(obj, "uses_severity", bool(meta.get("uses_severity", True)))
    setattr(obj, "supports_time_range", bool(meta.get("supports_time_range", True)))
    setattr(obj, "config_schema", list(meta.get("config_schema", []) or []))
    return obj


def register_fault_type(fault: FaultInjector) -> None:
    if not isinstance(fault, FaultInjector):
        raise TypeError("Fault type plugin must inherit BaseFaultType/FaultInjector.")
    name = str(getattr(fault, "name", "") or "").strip()
    if not name:
        raise ValueError("Fault type plugin must define a non-empty `name`.")
    if not FAULT_TYPE_NAME_PATTERN.fullmatch(name):
        raise ValueError("故障类型 name 只能包含字母、数字和下划线，且不能以数字开头")
    FAULT_TYPE_REGISTRY[name] = fault


def _register_builtins() -> None:
    for name, cls in _BUILTIN_CLASSES.items():
        register_fault_type(_apply_metadata(cls(), name, _BUILTIN_METADATA[name]))


def _dependency_status(names: list[str]) -> dict[str, Any]:
    missing: list[str] = []
    for name in names:
        try:
            if find_spec(name) is None:
                missing.append(name)
        except (ImportError, AttributeError, ValueError):
            missing.append(name)
    return {"installed": not missing, "missing": missing}


def available_fault_types() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, fault in FAULT_TYPE_REGISTRY.items():
        deps = list(getattr(fault, "dependencies", []) or [])
        items.append({
            "name": name,
            "label": getattr(fault, "label", name) or name,
            "description": getattr(fault, "description", ""),
            "formula": getattr(fault, "formula", ""),
            "dependencies": deps,
            "dependencies_status": _dependency_status(deps),
            "config_schema": list(getattr(fault, "config_schema", []) or []),
            "uses_severity": bool(getattr(fault, "uses_severity", True)),
            "supports_time_range": bool(getattr(fault, "supports_time_range", True)),
            "builtin": name in _BUILTIN_CLASSES,
        })
    return items


def get_fault_type(name: str) -> Optional[FaultInjector]:
    return FAULT_TYPE_REGISTRY.get(str(name or "").strip())


def create_fault_injector(name: str) -> FaultInjector:
    proto = get_fault_type(name)
    if proto is None:
        raise ValueError(
            f"Unknown fault type: {name}. Available: {list(FAULT_TYPE_REGISTRY.keys())}"
        )
    try:
        fresh = type(proto)()
    except Exception:  # pylint: disable=broad-exception-caught
        # Plugin contract expects a no-arg constructor, but deepcopy is a safe
        # fallback for already-instantiated legacy objects.
        return deepcopy(proto)
    for attr in _METADATA_FIELDS:
        if hasattr(proto, attr):
            setattr(fresh, attr, deepcopy(getattr(proto, attr)))
    return fresh


def discover_fault_types(search_dir: Optional[Path] = None) -> int:
    """Discover Python fault-type plugins from YAML descriptors."""
    global _DISCOVERED_CUSTOM_NAMES
    directory = Path(search_dir) if search_dir is not None else DEFAULT_CONFIG_DIR
    if search_dir is None or directory.resolve() == DEFAULT_CONFIG_DIR.resolve():
        for name in _DISCOVERED_CUSTOM_NAMES:
            if name not in _BUILTIN_CLASSES:
                FAULT_TYPE_REGISTRY.pop(name, None)
        _DISCOVERED_CUSTOM_NAMES.clear()
    if not directory.exists():
        return 0
    count = 0
    search_roots = [PROJECT_ROOT, directory, PROJECT_ROOT / "src" / "fault_plugins"]
    files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))
    for yaml_file in sorted(set(files), key=lambda p: p.name):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict) or not data.get("class_path"):
            continue
        try:
            cls = import_plugin_class(str(data["class_path"]), search_roots, force_reload=True)
            fault = cls()
            if not isinstance(fault, BaseFaultType):
                raise TypeError("custom fault type must inherit BaseFaultType")
            name = str(data.get("name") or getattr(fault, "name", "") or "").strip()
            if not name:
                raise ValueError("fault type descriptor requires name")
            meta = {
                "label": data.get("label", getattr(fault, "label", name)),
                "description": data.get("description", getattr(fault, "description", "")),
                "formula": data.get("formula", getattr(fault, "formula", "")),
                "dependencies": data.get("dependencies", getattr(fault, "dependencies", [])),
                "uses_severity": data.get("uses_severity", getattr(fault, "uses_severity", True)),
                "supports_time_range": data.get(
                    "supports_time_range", getattr(fault, "supports_time_range", True)
                ),
                "config_schema": data.get("config_schema", getattr(fault, "config_schema", [])),
            }
            _apply_metadata(fault, name, meta)
            register_fault_type(fault)
            if name not in _BUILTIN_CLASSES and (search_dir is None or directory.resolve() == DEFAULT_CONFIG_DIR.resolve()):
                _DISCOVERED_CUSTOM_NAMES.add(name)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            # A broken third-party descriptor must not hide built-in fault types.
            continue
    return count


def _coerce_value(raw: Any, field: dict[str, Any]) -> Any:
    kind = str(field.get("type") or "str").lower()
    if kind in {"float", "number"}:
        value = float(raw)
    elif kind in {"int", "integer"}:
        value = int(raw)
    elif kind in {"bool", "boolean"}:
        if isinstance(raw, bool):
            value = raw
        elif str(raw).strip().lower() in {"1", "true", "yes", "on"}:
            value = True
        elif str(raw).strip().lower() in {"0", "false", "no", "off"}:
            value = False
        else:
            raise ValueError("必须是布尔值")
    else:
        value = str(raw)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if field.get("min") is not None and value < float(field["min"]):
            raise ValueError(f"不能小于 {field['min']}")
        if field.get("max") is not None and value > float(field["max"]):
            raise ValueError(f"不能大于 {field['max']}")

    options = field.get("options")
    if isinstance(options, list) and options:
        allowed = []
        for option in options:
            allowed.append(option.get("value") if isinstance(option, dict) else option)
        if value not in allowed and str(value) not in {str(item) for item in allowed}:
            raise ValueError("不在允许选项中")
    return value


def normalize_fault_config(name: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """Apply descriptor defaults and validate common/custom fault parameters."""
    fault = get_fault_type(name)
    if fault is None:
        raise ValueError(f"未知故障类型: {name}")
    source = dict(config or {})
    result = dict(source)

    mode = str(result.get("mode", "full") or "full")
    if mode not in {"full", "time_range"}:
        raise ValueError("mode 只能是 full 或 time_range")
    if mode == "time_range" and not bool(getattr(fault, "supports_time_range", True)):
        raise ValueError("当前故障类型不支持指定时间段")
    result["mode"] = mode

    if bool(getattr(fault, "uses_severity", True)):
        try:
            severity = float(result.get("severity", 0.5))
        except (TypeError, ValueError) as exc:
            raise ValueError("故障程度 severity 必须是数值") from exc
        if severity < 0:
            raise ValueError("故障程度 severity 必须大于或等于 0")
        result["severity"] = severity

    for field in list(getattr(fault, "config_schema", []) or []):
        if not isinstance(field, dict):
            continue
        field_name = str(field.get("name") or field.get("key") or "").strip()
        if not field_name or field_name == "severity":
            continue
        present = field_name in result and result[field_name] not in (None, "")
        if not present:
            if "default" in field:
                result[field_name] = deepcopy(field["default"])
                present = True
            elif field.get("required"):
                raise ValueError(f"参数 {field.get('label', field_name)} 为必填项")
        if present:
            try:
                result[field_name] = _coerce_value(result[field_name], field)
            except Exception as exc:
                raise ValueError(f"参数 {field.get('label', field_name)} 无效: {exc}") from exc
    return result


def validate_fault_output(
    original: list[dict[str, Any]],
    faulty: Any,
) -> list[dict[str, Any]]:
    """Validate a plugin result before it is previewed or persisted."""
    if not isinstance(faulty, list):
        raise ValueError("故障类型插件 inject() 必须返回 list[dict]")
    if len(faulty) != len(original):
        raise ValueError(
            f"故障类型插件不得改变记录数：输入 {len(original)} 条，返回 {len(faulty)} 条"
        )
    if not original:
        return faulty
    expected_keys = set(original[0].keys())
    for index, row in enumerate(faulty):
        if not isinstance(row, dict):
            raise ValueError(f"故障类型插件第 {index + 1} 条结果不是对象")
        keys = set(row.keys())
        if keys != expected_keys:
            missing = sorted(expected_keys - keys)
            extra = sorted(keys - expected_keys)
            details = []
            if missing:
                details.append("缺少字段 " + ", ".join(missing))
            if extra:
                details.append("新增字段 " + ", ".join(extra))
            raise ValueError(
                f"故障类型插件不得改变数据表字段结构（第 {index + 1} 条：{'；'.join(details)}）"
            )
    return faulty


_register_builtins()
discover_fault_types()
