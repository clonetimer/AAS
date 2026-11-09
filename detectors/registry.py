from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any, Iterable

import yaml

from detectors.decision import (
    DECISION_MODE_LABEL,
    DECISION_MODE_NATIVE,
    DECISION_MODE_SCORE_THRESHOLD,
    normalize_decision_mode,
)

from core.plugin_loader import import_plugin_class
from core.native_plugins import is_native_descriptor, normalize_native_language, resolve_native_library_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs" / "detectors"
DEFAULT_CODE_DIR = PROJECT_ROOT / "detectors"

_BUILTIN_DETECTOR_REGISTRY: dict[str, str] = {
    "dummy_detector": "detectors.dummy_detector.DummyDetector",
    "isolation_forest_detector": "detectors.isolation_forest_detector.IsolationForestDetector",
    "lstm_autoencoder_detector": "detectors.lstm_autoencoder_detector.LSTMAutoEncoderDetector",
    "mlp_detector": "detectors.mlp_detector.MLPDetector",
    "three_sigma": "detectors.statistical_detectors.ThreeSigmaDetector",
    "zscore": "detectors.statistical_detectors.ZScoreDetector",
    "iqr": "detectors.statistical_detectors.IQRDetector",
    "torch_like_cusum": "detectors.torch_like_adapters.TorchLikeCUSUMDetector",
    "torch_like_ewma": "detectors.torch_like_adapters.TorchLikeEWMADetector",
    "torch_like_pca_spe": "detectors.torch_like_adapters.TorchLikePCASPEDetector",
    "torch_like_usad": "detectors.torch_like_neural.TorchLikeUSADDetector",
    "torch_like_tranad": "detectors.torch_like_neural.TorchLikeTranADDetector",
}
DETECTOR_REGISTRY: dict[str, str] = dict(_BUILTIN_DETECTOR_REGISTRY)

_BUILTIN_DETECTOR_METADATA: dict[str, dict[str, Any]] = {
    "three_sigma": {
        "label": "3-Sigma",
        "description": "基于 3-Sigma 原则的异常检测",
        "dependencies": [],
        "type": "statistical",
        "min_training_samples": 2,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    },
    "zscore": {
        "label": "Z-Score",
        "description": "基于 Z-Score 的异常检测",
        "dependencies": [],
        "type": "statistical",
        "min_training_samples": 2,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    },
    "iqr": {
        "label": "IQR",
        "description": "基于四分位距的异常检测",
        "dependencies": [],
        "type": "statistical",
        "min_training_samples": 4,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    },
    "isolation_forest_detector": {
        "label": "Isolation Forest",
        "description": "孤立森林异常检测",
        "dependencies": ["sklearn", "numpy"],
        "type": "machine_learning",
        "min_training_samples": 2,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold", "native"],
    },
    "mlp_detector": {
        "label": "MLP",
        "description": "多层感知机异常检测",
        "dependencies": ["torch", "numpy"],
        "type": "deep_learning",
        "min_training_samples": 32,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    },
    "lstm_autoencoder_detector": {
        "label": "LSTM AutoEncoder",
        "description": "LSTM 自编码器异常检测",
        "dependencies": ["torch", "numpy"],
        "type": "deep_learning",
        "min_training_samples": 32,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    },
}
for _name, _label, _kind, _minimum, _deps in (
    ("torch_like_cusum", "CUSUM", "statistical", 2, ["numpy"]),
    ("torch_like_ewma", "EWMA", "statistical", 2, ["numpy"]),
    ("torch_like_pca_spe", "PCA-SPE", "machine_learning", 4, ["numpy"]),
    ("torch_like_usad", "USAD", "deep_learning", 4, ["torch", "numpy"]),
    ("torch_like_tranad", "TranAD", "deep_learning", 4, ["torch", "numpy"]),
):
    _BUILTIN_DETECTOR_METADATA[_name] = {
        "label": _label,
        "description": "Adapted from clonetimer under Apache-2.0",
        "type": _kind,
        "dependencies": _deps,
        "min_training_samples": _minimum,
        "decision_mode": "score_threshold",
        "decision_modes": ["score_threshold"],
    }

DETECTOR_METADATA: dict[str, dict[str, Any]] = {
    name: dict(meta) for name, meta in _BUILTIN_DETECTOR_METADATA.items()
}

# Known parameters get curated UI constraints. Unknown parameters are inferred
# from defaults without invented min/max ranges.
PARAMETER_SCHEMA: dict[str, dict[str, Any]] = {
    "learning_rate": {"label": "学习率", "type": "float", "min": 0.00001, "max": 0.1, "step": 0.0001},
    "epochs": {"label": "训练轮数", "type": "int", "min": 1, "max": 500, "step": 1},
    "batch_size": {"label": "批大小", "type": "int", "min": 1, "max": 4096, "step": 1},
    "hidden_size": {"label": "隐藏层大小", "type": "int", "min": 1, "max": 4096, "step": 1},
    "hidden_sizes": {"label": "隐藏层尺寸列表", "type": "list", "description": "用逗号分隔，如: 64,32,16"},
    "n_estimators": {"label": "树数量", "type": "int", "min": 1, "max": 5000, "step": 1},
    "contamination": {"label": "异常比例", "type": "float", "min": 0.0, "max": 0.5, "step": 0.01},
    "random_state": {"label": "随机种子", "type": "int", "min": 0, "step": 1},
    "device": {"label": "设备", "type": "str", "options": ["cpu", "cuda"]},
    "verbose": {"label": "详细输出", "type": "bool"},
    "num_layers": {"label": "层数", "type": "int", "min": 1, "max": 100, "step": 1},
    "decision_mode": {
        "label": "判定方式", "type": "str",
        "options": ["score_threshold", "native", "label"],
        "description": "唯一最终判定权：分数+阈值 / 算法原生判定 / 直接标签。",
    },
    "threshold_method": {
        "label": "阈值策略", "type": "str",
        "options": ["fixed", "quantile", "online_quantile", "adaptive_ewma"],
        "description": "检测算法内部的阈值策略，不是独立插件。",
    },
    "threshold_window": {"label": "在线阈值窗口", "type": "int", "min": 2, "step": 1},
    "threshold_alpha": {"label": "自适应平滑系数", "type": "float", "min": 0.000001, "max": 1.0, "step": 0.01},
    "threshold_sigma": {"label": "自适应标准差倍数", "type": "float", "min": 0.0, "step": 0.1},
    "threshold_update_on_anomaly": {"label": "异常样本参与阈值更新", "type": "bool"},
}


def _config_dir(search_dir: str | Path | None = None) -> Path:
    return Path(search_dir).resolve() if search_dir is not None else DEFAULT_CONFIG_DIR


def _iter_descriptor_files(search_dir: str | Path | None = None) -> Iterable[Path]:
    directory = _config_dir(search_dir)
    if not directory.exists():
        return ()
    files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))
    return sorted(set(files), key=lambda p: p.name)


def _read_descriptor(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _find_descriptor(detector_name: str, search_dir: str | Path | None = None) -> tuple[Path | None, dict[str, Any] | None]:
    directory = _config_dir(search_dir)
    for suffix in (".yaml", ".yml"):
        direct = directory / f"{detector_name}{suffix}"
        if direct.is_file():
            data = _read_descriptor(direct)
            if data is not None:
                return direct, data
    for path in _iter_descriptor_files(search_dir):
        data = _read_descriptor(path)
        if data and str(data.get("name") or "").strip() == detector_name:
            return path, data
    return None, None


def get_detector_descriptor(detector_name: str, search_dir: str | Path | None = None) -> dict[str, Any]:
    """Return merged detector metadata and YAML descriptor for one algorithm."""
    path, descriptor = _find_descriptor(detector_name, search_dir)
    merged: dict[str, Any] = {"name": detector_name}
    if detector_name in DETECTOR_METADATA:
        merged.update(DETECTOR_METADATA[detector_name])
    if descriptor:
        merged.update(descriptor)
        merged["name"] = str(descriptor.get("name") or detector_name)
        if path is not None:
            merged["_descriptor_path"] = str(path)
    if "class_path" not in merged and detector_name in DETECTOR_REGISTRY:
        merged["class_path"] = DETECTOR_REGISTRY[detector_name]
    return merged


def _normalize_dependencies(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(k) for k in value.keys()]
    return [str(v) for v in value]


DEFAULT_MIN_TRAINING_SAMPLES = 2


def get_min_training_samples(
    detector_name: str,
    search_dir: str | Path | None = None,
    *,
    default: int = DEFAULT_MIN_TRAINING_SAMPLES,
) -> int:
    """Return the configured minimum number of valid samples for training.

    Built-in algorithms declare this in ``DETECTOR_METADATA``. Third-party
    detector descriptors may override it with a top-level
    ``min_training_samples`` field. Unknown plugins fall back to ``default``.
    """
    descriptor = get_detector_descriptor(detector_name, search_dir)
    raw = descriptor.get("min_training_samples", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(1, value)


def get_detector_dependencies(detector_name: str, search_dir: str | Path | None = None) -> list[str]:
    descriptor = get_detector_descriptor(detector_name, search_dir)
    return _normalize_dependencies(descriptor.get("dependencies", []))


def get_algorithm_type(algorithm_name: str) -> str:
    """Return detector type, honoring YAML metadata for dynamically added algorithms."""
    descriptor = get_detector_descriptor(algorithm_name)
    value = str(descriptor.get("type") or "").strip()
    if value:
        return value
    if descriptor.get("_descriptor_path") and algorithm_name not in _BUILTIN_DETECTOR_REGISTRY:
        return "custom"
    return "statistical"


def _infer_type(value: Any) -> str:
    # bool must be checked before int because bool is an int subclass in Python.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (list, tuple)):
        return "list"
    return "str"


def _normalize_schema_item(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any] | None:
    name = str(item.get("name") or item.get("key") or "").strip()
    if not name:
        return None
    default_present = "default" in item or name in defaults
    default = item.get("default", defaults.get(name))
    result: dict[str, Any] = {
        "name": name,
        "label": item.get("label", name.replace("_", " ").title()),
        "type": item.get("type") or _infer_type(default),
    }
    if default_present:
        result["default"] = default
    for key in ("min", "max", "step", "options", "description", "required", "placeholder", "visible_when"):
        if key in item:
            result[key] = item[key]
    return result


def get_detector_config_schema(detector_name: str, search_dir: str | Path | None = None) -> dict[str, Any]:
    """Build UI schema from explicit ``config_schema`` or safe type inference."""
    descriptor = get_detector_descriptor(detector_name, search_dir)
    defaults = descriptor.get("config", {}) or {}
    if not isinstance(defaults, dict):
        defaults = {}
    else:
        defaults = dict(defaults)
    # Legacy projects may still carry ``threshold_plugin`` in detector config.
    # It is only a compatibility alias now; never expose it as a plugin field.
    legacy_threshold_method = defaults.get("threshold_plugin")
    defaults.pop("threshold_plugin", None)
    if legacy_threshold_method is not None and "threshold_method" not in defaults:
        defaults["threshold_method"] = legacy_threshold_method

    declared_modes = descriptor.get("decision_modes")
    if isinstance(declared_modes, str):
        declared_modes = [declared_modes]
    if not isinstance(declared_modes, (list, tuple)) or not declared_modes:
        if detector_name == "isolation_forest_detector":
            declared_modes = ["score_threshold", "native"]
        else:
            declared_modes = [str(descriptor.get("decision_mode") or "score_threshold")]
    decision_modes = []
    for raw_mode in declared_modes:
        mode = str(raw_mode).strip().lower().replace("-", "_")
        if mode in {"score_threshold", "native", "label"} and mode not in decision_modes:
            decision_modes.append(mode)
    if not decision_modes:
        decision_modes = ["score_threshold"]
    default_decision_mode = str(
        defaults.get("decision_mode") or descriptor.get("decision_mode") or decision_modes[0]
    ).strip().lower().replace("-", "_")
    if default_decision_mode not in decision_modes:
        default_decision_mode = decision_modes[0]
    defaults["decision_mode"] = default_decision_mode

    schema: dict[str, Any] = {
        "name": detector_name,
        "parameters": [],
        "defaults": dict(defaults),
        "decision_modes": list(decision_modes),
        "decision_mode": default_decision_mode,
    }

    explicit = descriptor.get("config_schema")
    if isinstance(explicit, list):
        for raw in explicit:
            if isinstance(raw, dict):
                normalized = _normalize_schema_item(raw, defaults)
                if normalized is not None and normalized.get("name") != "threshold_plugin":
                    schema["parameters"].append(normalized)
        explicit_names = {p["name"] for p in schema["parameters"]}
    else:
        explicit_names = set()

    for name, default in defaults.items():
        if name in explicit_names:
            continue
        curated = dict(PARAMETER_SCHEMA.get(name, {}))
        param: dict[str, Any] = {
            "name": name,
            "label": curated.pop("label", name.replace("_", " ").title()),
            "type": curated.pop("type", _infer_type(default)),
            "default": default,
        }
        # Curated constraints are intentional. Unknown numeric parameters receive
        # no arbitrary min/max, so the browser cannot silently reject valid values.
        param.update(curated)
        schema["parameters"].append(param)

    # decision_mode is a detector capability declaration, not a free-form
    # parameter. Limit the UI to modes this detector descriptor supports.
    decision_param = next(
        (item for item in schema["parameters"] if item.get("name") == "decision_mode"),
        None,
    )
    if decision_param is None:
        decision_param = {
            "name": "decision_mode",
            "label": "判定方式",
            "type": "str",
            "default": default_decision_mode,
        }
        schema["parameters"].insert(0, decision_param)
    decision_param["options"] = list(decision_modes)
    decision_param["default"] = default_decision_mode
    decision_param["description"] = (
        "唯一最终判定权：score_threshold=检测器内阈值策略；"
        "native=算法原生判定；label=算法直接输出标签。"
    )

    # Thresholding belongs to the detector contract rather than the top-level
    # plugin system. Built-in BaseDetector provides reusable internal methods,
    # while a custom detector may override threshold()/predict() completely.
    parameter_names = {item["name"] for item in schema["parameters"]}
    default_threshold_method = (
        "quantile"
        if detector_name in {
            "isolation_forest_detector",
            "mlp_detector",
            "lstm_autoencoder_detector",
        }
        else "fixed"
    )
    threshold_parameters = [
        {
            "name": "threshold_method",
            "label": "阈值策略",
            "type": "str",
            "default": str(defaults.get("threshold_method", default_threshold_method)),
            "options": ["fixed", "quantile", "online_quantile", "adaptive_ewma"],
            "description": "检测算法内部的判定策略；在线/自适应策略会随数据流更新，不是独立插件。",
        },
        {
            "name": "threshold_window",
            "label": "在线阈值窗口",
            "type": "int",
            "default": int(defaults.get("threshold_window", 200)),
            "min": 2,
            "step": 1,
            "description": "online_quantile 使用的最近评分窗口长度。",
        },
        {
            "name": "threshold_alpha",
            "label": "自适应平滑系数",
            "type": "float",
            "default": float(defaults.get("threshold_alpha", 0.2)),
            "min": 0.000001,
            "max": 1.0,
            "step": 0.01,
            "description": "adaptive_ewma 的 EWMA 更新系数。",
        },
        {
            "name": "threshold_sigma",
            "label": "自适应标准差倍数",
            "type": "float",
            "default": float(defaults.get("threshold_sigma", 3.0)),
            "min": 0.0,
            "step": 0.1,
            "description": "adaptive_ewma 使用 mean + sigma × std 形成阈值。",
        },
        {
            "name": "threshold_update_on_anomaly",
            "label": "异常样本参与阈值更新",
            "type": "bool",
            "default": bool(defaults.get("threshold_update_on_anomaly", False)),
            "description": "默认关闭，避免异常点把在线阈值抬高。",
        },
    ]
    if "score_threshold" in decision_modes:
        for item in threshold_parameters:
            item["visible_when"] = {"name": "decision_mode", "value": "score_threshold"}
            if item["name"] not in parameter_names:
                schema["parameters"].append(item)
    return schema


def get_all_detector_schemas() -> dict[str, dict[str, Any]]:
    names = set(DETECTOR_METADATA) | set(DETECTOR_REGISTRY)
    for path in _iter_descriptor_files():
        data = _read_descriptor(path)
        if data and data.get("name"):
            names.add(str(data["name"]))
    return {name: get_detector_config_schema(name) for name in sorted(names)}


def _check_dependencies(deps: list[str]) -> dict[str, Any]:
    """Check whether dependency modules are installed without importing them.

    Importing heavy libraries (notably torch) while merely rendering the
    algorithm/plugin catalog can make the first Web request stall on Windows.
    Actual importability is still validated when a detector is loaded/run.
    """
    missing: list[str] = []
    for dep in deps:
        try:
            if find_spec(dep) is None:
                missing.append(dep)
        except (ImportError, AttributeError, ValueError):
            missing.append(dep)
    return {"installed": not missing, "missing": missing}


def get_available_algorithms() -> list[dict[str, Any]]:
    """Return detector catalog using YAML metadata as first-class plugin data."""
    descriptors: dict[str, dict[str, Any]] = {}
    for path in _iter_descriptor_files():
        data = _read_descriptor(path)
        if data and data.get("name"):
            descriptors[str(data["name"])] = data

    ordered_names: list[str] = list(DETECTOR_METADATA)
    for name in descriptors:
        if name not in ordered_names:
            ordered_names.append(name)

    algorithms: list[dict[str, Any]] = []
    for name in ordered_names:
        merged = dict(DETECTOR_METADATA.get(name, {}))
        descriptor = descriptors.get(name, {})
        merged.update(descriptor)
        dependencies = _normalize_dependencies(merged.get("dependencies", []))
        dep_status = _check_dependencies(dependencies)
        language = normalize_native_language(merged.get("language"))
        algorithms.append({
            "name": name,
            "label": merged.get("label", name.replace("_", " ").title()),
            "description": merged.get("description", ""),
            "type": merged.get("type", "statistical" if name in _BUILTIN_DETECTOR_METADATA else "custom"),
            "min_training_samples": get_min_training_samples(name),
            "decision_mode": merged.get("decision_mode", "score_threshold"),
            "decision_modes": merged.get("decision_modes", [merged.get("decision_mode", "score_threshold")]),
            # Backward-compatible field consumed by runner.js and existing API clients.
            "dependencies": dep_status,
            # Raw dependency names used by the unified PluginRuntime.
            "dependency_names": dependencies,
            "dependencies_status": dep_status,
            "config_schema": get_detector_config_schema(name).get("parameters", []),
            "class_path": merged.get("class_path") or DETECTOR_REGISTRY.get(name),
            "language": language,
            "abi": merged.get("abi"),
            "library": merged.get("library"),
            "native": language in {"c", "cpp"},
            "runtime_isolation": "separate_process" if language in {"c", "cpp"} else "in_process",
            "from_import": name not in _BUILTIN_DETECTOR_METADATA,
            "health": {"healthy": True, "missing_contract": []},
        })
    return algorithms


def _search_roots(search_dir: str | Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if search_dir is not None:
        directory = Path(search_dir).resolve()
        roots.append(directory)
        # A runtime rooted elsewhere (tests, embedded deployments) keeps the same
        # ``<root>/configs/detectors`` + ``<root>/detectors`` layout.
        if directory.name == "detectors" and directory.parent.name == "configs":
            runtime_root = directory.parent.parent
            for root in (runtime_root, runtime_root / "detectors"):
                if root not in roots:
                    roots.append(root)
    # Default project roots resolve both ``detectors.foo`` and legacy ``foo``.
    for root in (PROJECT_ROOT, DEFAULT_CODE_DIR):
        if root not in roots:
            roots.append(root)
    return roots


def import_class(class_path: str, search_dir: str | Path | None = None, *, force_reload: bool = False):
    try:
        return import_plugin_class(class_path, _search_roots(search_dir), force_reload=force_reload)
    except Exception as exc:
        raise ImportError(f"Failed to import class {class_path}: {exc}") from exc


def validate_detector_descriptor(
    descriptor: dict[str, Any],
    search_dir: str | Path | None = None,
    *,
    force_reload: bool = True,
) -> dict[str, Any]:
    """Validate the canonical detector plugin contract: BaseDetector + fit + score."""
    if not isinstance(descriptor, dict):
        raise ValueError("检测器配置必须是对象")
    name = str(descriptor.get("name") or "").strip()
    if not name:
        raise ValueError("配置缺少必填字段：name")

    if is_native_descriptor(descriptor):
        directory = _config_dir(search_dir)
        descriptor_path = descriptor.get("_descriptor_path")
        project_root = directory.parent.parent if directory.name == "detectors" and directory.parent.name == "configs" else PROJECT_ROOT
        library_path = resolve_native_library_path(
            descriptor,
            project_root=project_root,
            descriptor_path=descriptor_path,
        )
        from core.native_build import validate_native_library_isolated
        details = validate_native_library_isolated(project_root, library_path, "detectors")
        config = dict(descriptor.get("config") or {})
        mode = normalize_decision_mode(
            config.get("decision_mode", descriptor.get("decision_mode")),
            DECISION_MODE_SCORE_THRESHOLD,
        )
        capabilities = dict(details.get("capabilities") or {})
        if mode == DECISION_MODE_SCORE_THRESHOLD and not capabilities.get("score"):
            raise ValueError(
                "decision_mode=score_threshold 的 C/C++ detector 必须导出 aas_detector_score"
            )
        if mode == DECISION_MODE_NATIVE and not (
            capabilities.get("predict")
            or (capabilities.get("score") and capabilities.get("threshold"))
        ):
            raise ValueError(
                "decision_mode=native 的 C/C++ detector 必须导出 aas_detector_predict，"
                "或同时导出 aas_detector_score + aas_detector_threshold"
            )
        if mode == DECISION_MODE_LABEL and not capabilities.get("predict"):
            raise ValueError(
                "decision_mode=label 的 C/C++ detector 必须导出 aas_detector_predict"
            )
        methods = ["fit"]
        methods.extend(name for name in ("score", "predict", "threshold") if capabilities.get(name))
        return {
            "valid": True,
            "name": name,
            "language": normalize_native_language(descriptor.get("language")),
            "abi": descriptor.get("abi"),
            "library": str(library_path),
            "decision_mode": mode,
            "methods": methods,
            "capabilities": capabilities,
            "persistence": details.get("persistence"),
            "dependencies": _check_dependencies(_normalize_dependencies(descriptor.get("dependencies", []))),
            "message": "C/C++ 检测器 ABI 与判定模式验证通过",
        }

    class_path = str(descriptor.get("class_path") or "").strip()
    if not class_path:
        raise ValueError("Python 检测器配置缺少必填字段：class_path")
    cls = import_class(class_path, search_dir, force_reload=force_reload)
    # Resolve the base class at validation time.  Some embedding/test workflows
    # reload detectors.base; caching the old class object would reject a valid
    # plugin solely because of Python class identity after reload.
    from detectors import base as detector_base

    if not issubclass(cls, detector_base.BaseDetector):
        raise ValueError("检测器类必须继承 detectors.base.BaseDetector")
    overridden_final = [
        method_name
        for method_name in detector_base.BaseDetector._FRAMEWORK_OWNED_DECISION_METHODS  # pylint: disable=protected-access
        if getattr(cls, method_name) is not getattr(detector_base.BaseDetector, method_name)
    ]
    if overridden_final:
        raise ValueError(
            "检测器不能重写框架唯一判定入口："
            + ", ".join(f"{name}()" for name in overridden_final)
            + "；请按 decision_mode 实现 score()/native_predict()/label_predict()"
        )
    abstract = sorted(getattr(cls, "__abstractmethods__", set()))
    if abstract:
        raise ValueError(f"检测器仍有未实现的抽象方法：{', '.join(abstract)}")

    config = dict(descriptor.get("config") or {})
    mode = normalize_decision_mode(
        config.get("decision_mode", descriptor.get("decision_mode")),
        getattr(cls, "default_decision_mode", DECISION_MODE_SCORE_THRESHOLD),
    )
    supported = {
        normalize_decision_mode(v) for v in getattr(
            cls, "supported_decision_modes", (DECISION_MODE_SCORE_THRESHOLD,)
        )
    }
    if mode not in supported:
        raise ValueError(
            f"{cls.__name__} 不支持 decision_mode={mode!r}；支持：{sorted(supported)}"
        )
    if mode == DECISION_MODE_SCORE_THRESHOLD and cls.score is detector_base.BaseDetector.score:
        raise ValueError("decision_mode=score_threshold 必须实现 score()")
    if mode == DECISION_MODE_NATIVE and cls.native_predict is detector_base.BaseDetector.native_predict:
        raise ValueError("decision_mode=native 必须实现 native_predict()")
    if mode == DECISION_MODE_LABEL and cls.label_predict is detector_base.BaseDetector.label_predict:
        raise ValueError("decision_mode=label 必须实现 label_predict()")

    methods = ["fit"]
    if cls.score is not detector_base.BaseDetector.score:
        methods.append("score")
    if mode == DECISION_MODE_NATIVE:
        methods.append("native_predict")
    if mode == DECISION_MODE_LABEL:
        methods.append("label_predict")
    return {
        "valid": True,
        "name": name,
        "class_name": cls.__name__,
        "module_path": cls.__module__,
        "decision_mode": mode,
        "supported_decision_modes": sorted(supported),
        "methods": methods,
        "inherited_methods": ["detect", "predict", "score_and_predict"],
        "dependencies": _check_dependencies(_normalize_dependencies(descriptor.get("dependencies", []))),
        "message": "检测器接口与唯一判定模式验证通过",
    }


def discover_detectors(search_dir: str | Path | None = None) -> int:
    """Discover YAML-backed detectors and register healthy classes immediately."""
    directory = _config_dir(search_dir)
    if not directory.exists():
        return 0
    count = 0
    for path in _iter_descriptor_files(directory):
        descriptor = _read_descriptor(path)
        if not descriptor or not descriptor.get("name"):
            continue
        descriptor = dict(descriptor)
        descriptor["_descriptor_path"] = str(path)
        name = str(descriptor["name"]).strip()
        if not name:
            continue
        native = is_native_descriptor(descriptor)
        class_path = str(descriptor.get("class_path") or "").strip()
        if not native and not class_path:
            continue
        if not native and name in _BUILTIN_DETECTOR_REGISTRY and class_path != _BUILTIN_DETECTOR_REGISTRY[name]:
            # A plugin descriptor may tune built-in config, but must not replace the
            # built-in implementation under the same public name.
            continue
        try:
            validate_detector_descriptor(descriptor, directory, force_reload=True)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if native:
            # Native detectors are instantiated from their descriptor by
            # detectors.loader; no Python class path is inserted into the registry.
            DETECTOR_REGISTRY.pop(name, None)
        else:
            DETECTOR_REGISTRY[name] = class_path
        meta = dict(DETECTOR_METADATA.get(name, {}))
        for key in ("label", "description", "dependencies", "type", "config_schema", "persistence", "language", "abi", "library", "source", "decision_mode", "decision_modes"):
            if key in descriptor:
                meta[key] = descriptor[key]
        if class_path:
            meta["class_path"] = class_path
        else:
            meta.pop("class_path", None)
        meta["from_import"] = name not in _BUILTIN_DETECTOR_REGISTRY
        DETECTOR_METADATA[name] = meta
        count += 1
    return count


def load_detector_class(detector_spec: dict[str, Any]):
    name = str(detector_spec.get("name") or "")
    search_dir = detector_spec.get("_search_dir")
    if is_native_descriptor(detector_spec) or (name and is_native_descriptor(get_detector_descriptor(name, search_dir))):
        raise ValueError("C/C++ detector 由 detectors.loader 直接实例化，不提供 Python class")
    class_path = detector_spec.get("class_path")
    if class_path:
        builtin_path = _BUILTIN_DETECTOR_REGISTRY.get(name)
        force_reload = not (builtin_path and builtin_path == class_path)
        return import_class(str(class_path), search_dir, force_reload=force_reload)

    if name not in DETECTOR_REGISTRY:
        discover_detectors(search_dir)
    if name in DETECTOR_REGISTRY:
        class_path = DETECTOR_REGISTRY[name]
        force_reload = name not in _BUILTIN_DETECTOR_REGISTRY
        return import_class(class_path, search_dir, force_reload=force_reload)
    raise ValueError(f"Unknown detector: {name}")


def safe_load_detector_class(detector_spec: dict[str, Any]):
    try:
        return load_detector_class(detector_spec)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"Failed to load detector {detector_spec.get('name', 'unknown')}: {exc}")
        return None
