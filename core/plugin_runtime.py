"""Unified plugin runtime and capability catalog.

The project historically exposed one registry per plugin family.  That is kept
for backwards compatibility, while this module provides a small host layer
above those registries:

* one description for every plugin family;
* explicit contribution points and product surfaces;
* dependency/availability normalization;
* safe on-disk installation and immediate discovery;
* a capability snapshot that the UI can consume directly.

This is intentionally framework-light so the existing pure Python + vanilla JS
stack does not need a build system or a plugin SDK migration.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

from dataclasses import dataclass
import base64
import hashlib
import importlib
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
import ast
import json
import re
import sys
import tempfile
from typing import Any

import yaml

from core.native_plugins import (
    NATIVE_ABI_V1, NATIVE_PLUGIN_TYPES, is_native_descriptor,
    native_descriptor_contract_errors, native_library_suffixes,
    normalize_native_language,
)


@dataclass(frozen=True)
class PluginTypeSpec:
    key: str
    label: str
    module: str
    available_func: str
    discover_func: str | None
    config_dir: str
    contribution_point: str
    surfaces: tuple[str, ...]
    activation_events: tuple[str, ...]
    installable: bool = True
    code_dir: str | None = None


PLUGIN_TYPES: tuple[PluginTypeSpec, ...] = (
    PluginTypeSpec(
        "metrics", "评估指标", "evaluator.metrics_plugins.registry",
        "available_metrics", "discover_metrics", "configs/metrics",
        "evaluation.metrics", ("算法运行", "结果报表"), ("on:evaluation",),
    ),
    PluginTypeSpec(
        "datasources", "数据源", "evaluator.datasource_plugins.registry",
        "available_datasources", "discover_datasources", "configs/datasources",
        "data.sources", ("配置驱动实验", "算法运行"), ("on:dataset-load",),
    ),
    PluginTypeSpec(
        "preprocessors", "预处理算子", "scripts.core.preproc_plugins.registry",
        "available_operators", "discover_preprocessors", "configs/preprocessors",
        "data.preprocessing", ("数据浏览", "算法运行"), ("on:preprocessing",),
    ),
    PluginTypeSpec(
        "reports", "报告格式", "evaluator.report_plugins.registry",
        "available_renderers", "discover_reports", "configs/reports",
        "report.renderers", ("结果报表",), ("on:report-export",),
    ),
    PluginTypeSpec(
        "runners", "执行后端", "runner.runner_plugins.registry",
        "available_runners", "discover_runners", "configs/runners",
        "execution.runners", ("算法运行",), ("on:task-run",),
    ),
    PluginTypeSpec(
        "training_strategies", "训练策略", "scripts.core.training_plugins.registry",
        "available_strategies", "discover_strategies", "configs/training_strategies",
        "training.strategies", ("算法运行",), ("on:training",),
    ),
    PluginTypeSpec(
        "fault_types", "故障类型", "src.fault_plugins.registry",
        "available_fault_types", "discover_fault_types", "configs/fault_types",
        "simulation.fault_types", ("模拟数据库",), ("on:fault-injection",), True, "src/fault_plugins",
    ),
    PluginTypeSpec(
        "detectors", "检测算法", "detectors.registry",
        "get_available_algorithms", "discover_detectors", "configs/detectors",
        "detection.algorithms", ("算法运行",), ("on:detection",), True, "detectors",
    ),
)

PLUGIN_TYPE_MAP = {spec.key: spec for spec in PLUGIN_TYPES}

REGISTRY_ATTRS = {
    "metrics": "METRIC_REGISTRY",
    "datasources": "DATASOURCE_REGISTRY",
    "preprocessors": "PREPROCESS_REGISTRY",
    "reports": "REPORT_REGISTRY",
    "runners": "RUNNER_REGISTRY",
    "training_strategies": "TRAINING_STRATEGY_REGISTRY",
    "fault_types": "FAULT_TYPE_REGISTRY",
    "detectors": "DETECTOR_REGISTRY",
}

# Built-ins are protected from uninstall.  User-installed YAML descriptors live
# in the same config directories as the detector defaults, so detectors need an
# explicit protected-name list; the other families are also listed here to make
# the policy visible and deterministic rather than relying on class-module
# heuristics.
BUILTIN_PLUGIN_NAMES: dict[str, set[str]] = {
    "metrics": {
        "precision", "recall", "f1", "accuracy", "false_alarm_rate",
        "roc_auc", "pr_auc",
    },
    "datasources": {"csv", "sqlite"},
    "preprocessors": {"fill_missing", "repair_outlier", "smooth", "normalize"},
    "reports": {"json", "csv", "markdown"},
    "runners": {"local"},
    "training_strategies": {"standard"},
    "fault_types": {"spike", "drift", "break", "periodic", "noise"},
    "detectors": {
        "dummy_detector", "isolation_forest_detector",
        "lstm_autoencoder_detector", "mlp_detector", "three_sigma",
        "zscore", "iqr",
        # Phase 9 adapters are shipped with AAS and are NOT user-installed
        # plugins. Their YAML descriptors must never be offered for deletion.
        "torch_like_cusum", "torch_like_ewma", "torch_like_pca_spe",
        "torch_like_usad", "torch_like_tranad",
    },
}

INSTALL_MANIFEST = "configs/.aas_plugin_installations.json"

REQUIRED_MEMBERS = {
    "metrics": ("name", "compute"),
    "datasources": ("formats", "read_rows"),
    "preprocessors": ("name", "enabled", "apply"),
    "reports": ("name", "render"),
    "runners": ("name", "run"),
    "training_strategies": ("name", "execute"),
    "fault_types": ("name", "inject"),
}


def _plugin_base_class(ptype: str) -> type[Any]:
    """Resolve the canonical base class lazily to avoid import cycles."""
    if ptype == "metrics":
        from evaluator.metrics_plugins.base import BaseMetric
        plugin_base = BaseMetric
    elif ptype == "datasources":
        from evaluator.datasource_plugins.base import BaseDataSource
        plugin_base = BaseDataSource
    elif ptype == "preprocessors":
        from scripts.core.preproc_plugins.base import BasePreprocessOperator
        plugin_base = BasePreprocessOperator
    elif ptype == "reports":
        from evaluator.report_plugins.base import BaseReportRenderer
        plugin_base = BaseReportRenderer
    elif ptype == "runners":
        from runner.runner_plugins.base import BaseRunner
        plugin_base = BaseRunner
    elif ptype == "training_strategies":
        from scripts.core.training_plugins.base import BaseTrainingStrategy
        plugin_base = BaseTrainingStrategy
    elif ptype == "fault_types":
        from src.fault_plugins.base import BaseFaultType
        plugin_base = BaseFaultType
    elif ptype == "detectors":
        from detectors.base import BaseDetector
        plugin_base = BaseDetector
    else:
        raise ValueError(f"不支持的插件类型: {ptype}")
    return plugin_base


def _normalize_dependency_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(key) for key in value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    raise ValueError("dependencies 必须是字符串、列表或对象")


STATIC_PLUGIN_CONTRACTS: dict[str, dict[str, Any]] = {
    "metrics": {"base": "evaluator.metrics_plugins.base.BaseMetric", "methods": ("compute",), "call_arity": {"compute": 2}},
    "datasources": {"base": "evaluator.datasource_plugins.base.BaseDataSource", "methods": ("read_rows",), "call_arity": {"read_rows": 2}},
    "preprocessors": {"base": "scripts.core.preproc_plugins.base.BasePreprocessOperator", "methods": ("apply",), "call_arity": {"apply": 3}},
    "reports": {"base": "evaluator.report_plugins.base.BaseReportRenderer", "methods": ("render",), "call_arity": {"render": 4}},
    "runners": {"base": "runner.runner_plugins.base.BaseRunner", "methods": ("run",), "call_arity": {"run": 2}},
    "training_strategies": {"base": "scripts.core.training_plugins.base.BaseTrainingStrategy", "methods": ("execute",), "call_arity": {"execute": 6}},
    "fault_types": {"base": "src.fault_plugins.base.BaseFaultType", "methods": ("inject",), "call_arity": {"inject": 3}},
    "detectors": {"base": "detectors.base.BaseDetector", "methods": ("fit", "score"), "call_arity": {"fit": 2, "score": 2}},
}


def _ast_import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    # ``import a.b`` binds ``a`` in local scope.
                    aliases[alias.name.split(".", 1)[0]] = alias.name.split(".", 1)[0]
    return aliases


def _ast_expr_path(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _ast_expr_path(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _static_validate_plugin_source(ptype: str, class_path: str, code_content: str, filename: str) -> dict[str, Any]:
    """Validate source structure without executing untrusted plugin code."""
    tree = ast.parse(code_content, filename=filename, mode="exec")
    class_name = class_path.rsplit(".", 1)[-1]
    class_node = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
        None,
    )
    if class_node is None:
        raise ValueError(f"代码中未找到类: {class_name}")

    contract = STATIC_PLUGIN_CONTRACTS[ptype]
    expected_base = str(contract["base"])
    aliases = _ast_import_aliases(tree)
    bases = [_ast_expr_path(base, aliases) for base in class_node.bases]
    base_ok = any(base == expected_base for base in bases if base)
    if not base_ok:
        raise ValueError(f"插件类必须继承 {expected_base}")

    methods = {
        node.name for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_methods = [name for name in contract["methods"] if name not in methods]
    if missing_methods:
        raise ValueError("代码缺少必需方法: " + ", ".join(missing_methods))

    call_arity = contract.get("call_arity", {})
    for method_name, expected_total_args in call_arity.items():
        method_node = next(
            (node for node in class_node.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name),
            None,
        )
        if method_node is None:
            continue
        args = method_node.args
        positional_count = len(args.posonlyargs) + len(args.args)
        required_positional = positional_count - len(args.defaults)
        required_kwonly = sum(default is None for default in args.kw_defaults)
        accepts_expected = (
            required_positional <= expected_total_args
            and (args.vararg is not None or expected_total_args <= positional_count)
            and required_kwonly == 0
        )
        if not accepts_expected:
            raise ValueError(
                f"方法 {method_name} 的参数签名与宿主调用不兼容；"
                f"需要能接收 {expected_total_args - 1} 个业务位置参数（另含 self）"
            )

    return {
        "class_name": class_name,
        "base_class": expected_base,
        "base_expressions": bases,
        "required_methods": list(contract["methods"]),
        "syntax_ok": True,
        "static_contract": True,
    }


def _check_deps(deps: Any) -> dict[str, Any]:
    """Normalize dependency status to ``{installed, missing}``."""
    if isinstance(deps, dict):
        return {
            "installed": bool(deps.get("installed", not deps.get("missing"))),
            "missing": list(deps.get("missing", [])),
        }
    missing: list[str] = []
    for dep in deps or []:
        name = str(dep)
        try:
            # Catalog endpoints only need to know whether a dependency is
            # installed. Importing large runtimes such as PyTorch here can block
            # the first /api/plugins request for many seconds on Windows.
            if find_spec(name) is None:
                missing.append(name)
        except (ImportError, AttributeError, ValueError):
            missing.append(name)
    return {"installed": not missing, "missing": missing}


def _safe_filename(filename: str, suffixes: tuple[str, ...]) -> str:
    """Return a safe basename or raise for traversal/unsupported suffixes."""
    raw = (filename or "").strip()
    if not raw:
        raise ValueError("文件名不能为空")
    candidate = Path(raw)
    if candidate.name != raw or raw in {".", ".."} or re.search(r'[\\/:*?"<>|\x00-\x1f]', raw):
        raise ValueError("文件名不能包含目录路径")
    if candidate.suffix.lower() not in suffixes:
        raise ValueError(f"不支持的文件扩展名: {candidate.suffix or '(无)'}")
    return raw




def _safe_native_dir_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("插件名称不能为空")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-") or "plugin"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{safe[:48]}-{digest}"


def _decode_base64_file(content: str | None) -> bytes:
    raw = str(content or "").strip()
    if not raw:
        return b""
    # FileReader payloads may be sent either as raw base64 or a data URL.
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("原生库内容不是有效 Base64") from exc

def _validate_object(ptype: str, obj: Any) -> dict[str, Any]:
    missing: list[str] = []
    for member in REQUIRED_MEMBERS.get(ptype, ()):
        if not hasattr(obj, member):
            missing.append(member)
    return {
        "healthy": not missing,
        "missing_contract": missing,
    }


def _safe_available_items(spec: PluginTypeSpec, module: Any) -> tuple[list[dict[str, Any]], str]:
    try:
        return [dict(item) for item in getattr(module, spec.available_func)()], ""
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # A malformed third-party object must not take down the entire catalog.
        registry = getattr(module, REGISTRY_ATTRS.get(spec.key, ""), {})
        if not isinstance(registry, dict):
            return [], f"{type(exc).__name__}: {exc}"
        items: list[dict[str, Any]] = []
        for key, obj in registry.items():
            name = getattr(obj, "name", None) or str(key)
            item = {
                "name": name,
                "format": str(key) if spec.key == "datasources" else None,
                "label": getattr(obj, "label", name),
                "description": getattr(obj, "description", ""),
                "dependencies": getattr(obj, "dependencies", []),
                "config_schema": getattr(obj, "config_schema", []),
                "order": getattr(obj, "order", None),
                "default_enabled": getattr(obj, "default_enabled", None),
            }
            item["health"] = _validate_object(spec.key, obj)
            items.append(item)
        return items, f"{type(exc).__name__}: {exc}"


def _normalize_item(spec: PluginTypeSpec, item: dict[str, Any]) -> dict[str, Any]:
    name = item.get("name") or item.get("format") or ""
    deps_value = item.get("dependencies", []) or []
    deps = item.get("dependency_names", deps_value) or []
    dep_status = dict(item.get("dependencies_status") or _check_deps(deps_value))
    # Some registries already expose an availability check beyond Python deps.
    if item.get("available") is False:
        dep_status["installed"] = False
    return {
        "name": name,
        "label": item.get("label", name),
        "description": item.get("description", ""),
        "dependencies": deps,
        "dependencies_status": dep_status,
        "config_schema": item.get("config_schema", []) or [],
        "order": item.get("order"),
        "default_enabled": item.get("default_enabled"),
        "file_extension": item.get("file_extension"),
        "media_type": item.get("media_type"),
        "display_format": item.get("display_format"),
        "display_precision": item.get("display_precision"),
        "formats": list(item.get("formats") or []),
        "language": normalize_native_language(item.get("language")),
        "abi": item.get("abi"),
        "library": item.get("library"),
        "native": bool(item.get("native", False)),
        "contributes": [
            {
                "point": spec.contribution_point,
                "surfaces": list(spec.surfaces),
            }
        ],
        "activation_events": list(spec.activation_events),
        "health": item.get("health", {"healthy": True, "missing_contract": []}),
    }


def _read_descriptor_file(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _descriptor_files(root: Path, spec: PluginTypeSpec) -> list[tuple[Path, dict[str, Any]]]:
    directory = root / spec.config_dir
    if not directory.exists():
        return []
    out: list[tuple[Path, dict[str, Any]]] = []
    files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))
    for path in sorted(set(files), key=lambda item: item.name):
        data = _read_descriptor_file(path)
        if data is not None:
            out.append((path, data))
    return out


def _descriptor_matches(ptype: str, descriptor: dict[str, Any], item_name: str) -> bool:
    descriptor_name = str(descriptor.get("name") or "").strip()
    if descriptor_name == item_name:
        return True
    if ptype == "datasources":
        return item_name in {str(fmt).strip() for fmt in descriptor.get("formats", []) or []}
    return False


def _find_descriptor(
    root: Path,
    spec: PluginTypeSpec,
    ptype: str,
    item_name: str,
) -> tuple[Path, dict[str, Any]] | None:
    records = _descriptor_files(root, spec)
    exact = [record for record in records if str(record[1].get("name") or "").strip() == item_name]
    if exact:
        return exact[0]
    matched = [record for record in records if _descriptor_matches(ptype, record[1], item_name)]
    if len(matched) == 1:
        return matched[0]
    return None


def _contains_exact_value(value: Any, names: set[str]) -> bool:
    if isinstance(value, str):
        return value.strip() in names
    if isinstance(value, dict):
        return any(_contains_exact_value(v, names) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_exact_value(v, names) for v in value)
    return False


class PluginRuntime:
    """Adapter layer over the project's existing family-specific registries."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @property
    def _manifest_path(self) -> Path:
        return self.root / INSTALL_MANIFEST

    def _load_manifest(self) -> dict[str, Any]:
        path = self._manifest_path
        if not path.exists():
            return {"version": 1, "plugins": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"version": 1, "plugins": {}}
        if not isinstance(data, dict):
            return {"version": 1, "plugins": {}}
        plugins = data.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}
        return {"version": 1, "plugins": plugins}

    def _save_manifest(self, data: dict[str, Any]) -> None:
        path = self._manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        )
        tmp.replace(path)

    @staticmethod
    def _manifest_key(ptype: str, name: str) -> str:
        return f"{ptype}:{name}"

    def _record_installation(
        self,
        ptype: str,
        name: str,
        yaml_path: Path,
        code_path: Path | None = None,
        *,
        library_path: Path | None = None,
        source_path: Path | None = None,
        language: str = "python",
    ) -> None:
        data = self._load_manifest()
        key = self._manifest_key(ptype, name)
        previous = dict(data["plugins"].get(key) or {})
        entry = {
            "type": ptype,
            "name": name,
            "language": language,
            "yaml": yaml_path.relative_to(self.root).as_posix(),
            "code": previous.get("code", ""),
            "library": previous.get("library", ""),
            "source": previous.get("source", ""),
        }
        if code_path is not None:
            entry["code"] = code_path.relative_to(self.root).as_posix()
        if library_path is not None:
            entry["library"] = library_path.relative_to(self.root).as_posix()
        if source_path is not None:
            entry["source"] = source_path.relative_to(self.root).as_posix()
        data["plugins"][key] = entry
        self._save_manifest(data)

    def _path_is_unlinked_inside_root(self, path: Path) -> bool:
        """Reject traversal and any symlink below the trusted project root."""
        root = self.root.absolute()
        try:
            relative = path.absolute().relative_to(root)
        except ValueError:
            return False
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        return is_relative_to(path, self.root)

    def _check_install_target(
        self, ptype: str, name: str, yaml_path: Path,
        target: Path, field: str,
    ) -> None:
        """Prevent an upload from replacing another plugin or project source."""
        if not self._path_is_unlinked_inside_root(target):
            raise ValueError(f"安装目标存在符号链接或越界路径: {target}")
        if field == "yaml" and target.stem in BUILTIN_PLUGIN_NAMES.get(ptype, set()):
            raise ValueError("内置插件描述文件禁止覆盖")
        if not target.exists():
            return
        # A repeat install may update only artifacts owned by the same plugin.
        manifest = self._load_manifest()
        entry = manifest.get("plugins", {}).get(self._manifest_key(ptype, name)) or {}
        rel = target.relative_to(self.root).as_posix()
        expected_yaml = yaml_path.relative_to(self.root).as_posix()
        if (not isinstance(entry, dict) or entry.get("name") != name
                or entry.get("type") != ptype or entry.get("yaml") != expected_yaml
                or entry.get(field) != rel):
            raise ValueError(f"安装目标已经存在且不属于该插件: {rel}")
        if self._referenced_in_other_manifests(manifest, ptype, name, rel):
            raise ValueError(f"安装目标被其他插件共享，拒绝覆盖: {rel}")
        if field == "yaml":
            current = _read_descriptor_file(target)
            if current is None or str(current.get("name") or "").strip() != name:
                raise ValueError("现有描述文件与插件身份不匹配")

    def _manifest_owned_path(
        self,
        spec: PluginTypeSpec,
        name: str,
        descriptor_path: Path,
        descriptor: dict[str, Any],
        entry: dict[str, Any],
        field: str,
    ) -> tuple[str, Path | None]:
        """Trust a recorded artifact only within this plugin's exact namespace.

        Installation manifests are metadata, not filesystem deletion authority.
        The expected path is reconstructed from the descriptor and installer
        layout, rather than treating an in-root path as safe to unlink.
        """
        rel = entry.get(field)
        if not isinstance(rel, str) or not rel or field not in {"code", "library", "source"}:
            return "", None
        if (entry.get("type") != spec.key or entry.get("name") != name
                or entry.get("yaml") != descriptor_path.relative_to(self.root).as_posix()):
            return rel, None

        expected = ""
        if field == "code" and normalize_native_language(descriptor.get("language")) == "python":
            class_path = str(descriptor.get("class_path") or "")
            parts = class_path.split(".")
            if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for v in parts):
                expected = f"{spec.code_dir or spec.config_dir}/{parts[-2]}.py"
        elif field in {"library", "source"} and normalize_native_language(descriptor.get("language")) in {"c", "cpp"}:
            # Native artifacts must also be named in the active descriptor.
            if descriptor.get(field) == rel:
                parsed = PurePosixPath(rel)
                extension = parsed.suffix.lower()
                allowed = (native_library_suffixes() if field == "library" else
                           ((".c",) if descriptor.get("language") == "c" else (".cpp", ".cc", ".cxx")))
                if extension in allowed:
                    expected = (f"native_plugins/{spec.key}/{_safe_native_dir_name(name)}/"
                                f"{parsed.name}")
        if not expected or rel != expected:
            return rel, None
        if field == "code" and PurePosixPath(rel).stem in {
            "__init__", "base", "builtin", "registry", "loader", "dependencies",
            "decision", "thresholding", *BUILTIN_PLUGIN_NAMES.get(spec.key, set()),
        }:
            return rel, None
        candidate = self.root / rel
        if not self._path_is_unlinked_inside_root(candidate) or not candidate.is_file():
            return rel, None
        return rel, candidate

    @staticmethod
    def _referenced_in_other_manifests(
        manifest: dict[str, Any], ptype: str, name: str, rel: str
    ) -> bool:
        """Retain files claimed by other installations even without YAML descriptors."""
        own_key = f"{ptype}:{name}"
        return any(
            other_key != own_key and isinstance(entry, dict)
            and rel in (entry.get("code"), entry.get("library"), entry.get("source"))
            for other_key, entry in manifest.get("plugins", {}).items()
        )

    def _remove_installation_record(self, ptype: str, name: str) -> dict[str, Any] | None:
        data = self._load_manifest()
        entry = data["plugins"].pop(self._manifest_key(ptype, name), None)
        self._save_manifest(data)
        return dict(entry) if isinstance(entry, dict) else None

    def _config_references(
        self,
        ptype: str,
        descriptor: dict[str, Any],
        descriptor_path: Path,
    ) -> list[str]:
        names = {str(descriptor.get("name") or "").strip()}
        if ptype == "datasources":
            names.update(str(fmt).strip() for fmt in descriptor.get("formats", []) or [])
        names.discard("")
        if not names:
            return []

        refs: list[str] = []
        configs_root = self.root / "configs"
        if not configs_root.exists():
            return refs
        candidates = (
            list(configs_root.rglob("*.yaml"))
            + list(configs_root.rglob("*.yml"))
            + list(configs_root.rglob("*.json"))
        )
        for path in sorted(set(candidates)):
            if path in (descriptor_path, self._manifest_path):
                continue
            try:
                if path.suffix.lower() == ".json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                else:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError):
                continue
            if _contains_exact_value(data, names):
                refs.append(path.relative_to(self.root).as_posix())
        return refs

    def _code_referenced_elsewhere(
        self,
        spec: PluginTypeSpec,
        descriptor_path: Path,
        class_path: str,
    ) -> bool:
        module_name = class_path.rsplit(".", 1)[0] if "." in class_path else class_path
        if not module_name:
            return False
        for path, data in _descriptor_files(self.root, spec):
            if path == descriptor_path:
                continue
            other = str(data.get("class_path") or "").strip()
            if other and other.rsplit(".", 1)[0] == module_name:
                return True
        return False

    def _descriptor_artifact_referenced_elsewhere(
        self,
        spec: PluginTypeSpec,
        descriptor_path: Path,
        field: str,
        value: str,
    ) -> bool:
        target = str(value or "").strip()
        if not target:
            return False
        for path, data in _descriptor_files(self.root, spec):
            if path == descriptor_path:
                continue
            if str(data.get(field) or "").strip() == target:
                return True
        return False

    def _ensure_runtime_builtins(self) -> None:
        # LocalRunner is intentionally lazily registered to avoid import cycles.
        try:
            from runner.runner_plugins.builtin import register_builtin_runners

            register_builtin_runners()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def collect(self) -> dict[str, dict[str, Any]]:
        self._ensure_runtime_builtins()
        manifest = self._load_manifest().get("plugins", {})
        out: dict[str, dict[str, Any]] = {}
        for spec in PLUGIN_TYPES:
            module = importlib.import_module(spec.module)
            raw_items, catalog_error = _safe_available_items(spec, module)
            registry = getattr(module, REGISTRY_ATTRS.get(spec.key, ""), {})
            items = []
            for raw in raw_items:
                item = _normalize_item(spec, dict(raw))
                if isinstance(registry, dict) and spec.key != "detectors":
                    obj = registry.get(item["name"])
                    if obj is None and spec.key == "datasources":
                        obj = registry.get(raw.get("format"))
                    if obj is not None:
                        item["health"] = _validate_object(spec.key, obj)
                descriptor_record = _find_descriptor(self.root, spec, spec.key, item["name"])
                descriptor = descriptor_record[1] if descriptor_record else None
                descriptor_name = str((descriptor or {}).get("name") or item["name"]).strip()
                if descriptor:
                    item["language"] = normalize_native_language(descriptor.get("language"))
                    item["abi"] = descriptor.get("abi")
                    item["library"] = descriptor.get("library")
                    item["native"] = is_native_descriptor(descriptor)
                    if item["native"]:
                        item["runtime_isolation"] = "separate_process"
                builtin = item["name"] in BUILTIN_PLUGIN_NAMES.get(spec.key, set())
                if spec.key == "datasources" and descriptor_name in BUILTIN_PLUGIN_NAMES.get(spec.key, set()):
                    builtin = True
                item["source"] = "builtin" if builtin else ("custom" if descriptor_record else "runtime")
                item["builtin"] = builtin
                item["uninstallable"] = bool(descriptor_record and not builtin)
                item["descriptor_name"] = descriptor_name
                item["managed_install"] = self._manifest_key(spec.key, descriptor_name) in manifest
                items.append(item)
            out[spec.key] = {
                "label": spec.label,
                "items": items,
                "installable": spec.installable,
                "contribution_point": spec.contribution_point,
                "surfaces": list(spec.surfaces),
                "activation_events": list(spec.activation_events),
                "catalog_error": catalog_error,
            }
        return out

    def capability_snapshot(self) -> dict[str, Any]:
        catalog = self.collect()
        surfaces: dict[str, list[dict[str, str]]] = {}
        points: dict[str, dict[str, Any]] = {}
        total = available = degraded = 0

        for ptype, group in catalog.items():
            point = group["contribution_point"]
            point_items = []
            for item in group["items"]:
                total += 1
                if item["dependencies_status"]["installed"] and item.get("health", {}).get("healthy", True):
                    available += 1
                else:
                    degraded += 1
                point_items.append({"type": ptype, "name": item["name"], "label": item["label"]})
                for surface in group["surfaces"]:
                    surfaces.setdefault(surface, []).append(
                        {"type": ptype, "name": item["name"], "label": item["label"], "point": point}
                    )
            points[point] = {"type": ptype, "label": group["label"], "items": point_items}

        return {
            "summary": {"total": total, "available": available, "degraded": degraded},
            "contribution_points": points,
            "surfaces": surfaces,
        }

    def _preview_native(
        self,
        ptype: str,
        yaml_filename: str,
        yaml_content: str,
        *,
        language: str,
        library_filename: str | None,
        library_content_base64: str | None,
        source_filename: str | None,
        source_content: str | None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = [
            "C/C++ 原生插件会在独立 Native Plugin Host 进程中执行，不直接加载到 AAS 主服务；仍应仅导入可信来源的本地插件。"
        ]
        descriptor: dict[str, Any] = {}
        dep_status = {"installed": True, "missing": []}
        dependency_names: list[str] = []
        normalized_yaml_name = str(yaml_filename or "")
        normalized_library_name: str | None = None
        normalized_source_name: str | None = None
        native_info: dict[str, Any] = {
            "provided": bool(library_filename or library_content_base64),
            "language": language,
            "abi": NATIVE_ABI_V1,
        }

        if ptype not in NATIVE_PLUGIN_TYPES:
            return {
                "status": "success", "valid": False,
                "errors": ["C/C++ 原生插件首期仅支持检测算法和评估指标"],
                "warnings": warnings,
            }

        try:
            normalized_yaml_name = _safe_filename(yaml_filename, (".yaml", ".yml"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(str(exc))

        try:
            raw = yaml.safe_load(yaml_content or "")
            if not isinstance(raw, dict):
                raise ValueError("YAML 内容必须是对象")
            descriptor = dict(raw)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(f"YAML 解析失败: {exc}")

        name = str(descriptor.get("name") or "").strip()
        if descriptor:
            if not name:
                errors.append("YAML 必须声明 name")
            descriptor_language = normalize_native_language(descriptor.get("language"))
            if descriptor_language != language:
                errors.append(
                    f"实现语言选择为 {language}，但 YAML language={descriptor.get('language')!r}；请保持一致"
                )
            errors.extend(native_descriptor_contract_errors(ptype, descriptor))
            if "config" in descriptor and not isinstance(descriptor.get("config"), dict):
                errors.append("config 必须是对象")
            if "config_schema" in descriptor and not isinstance(descriptor.get("config_schema"), list):
                errors.append("config_schema 必须是列表")
            try:
                dependency_names = _normalize_dependency_names(descriptor.get("dependencies", []))
                dep_status = _check_deps(dependency_names)
                if dep_status["missing"]:
                    warnings.append("当前 Python 宿主环境缺少依赖: " + ", ".join(dep_status["missing"]))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(str(exc))

        if source_filename or source_content:
            if not (source_filename and source_content):
                errors.append("C/C++ 源文件名与源码内容必须同时提供")
            else:
                try:
                    suffixes = (".c",) if language == "c" else (".cpp", ".cc", ".cxx")
                    normalized_source_name = _safe_filename(source_filename, suffixes)
                    native_info["source_filename"] = normalized_source_name
                    if "aas_plugin_abi" not in source_content or "aas_plugin_kind" not in source_content:
                        warnings.append("源码中未识别到 aas_plugin_abi / aas_plugin_kind；请确认导出 ABI 函数。")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    errors.append(str(exc))

        if not (library_filename and library_content_base64):
            errors.append(
                "请先使用“本机隔离编译”生成原生库，或导入已编译的原生库（当前系统需要 "
                + "/".join(native_library_suffixes()) + "）。"
            )
        else:
            try:
                normalized_library_name = _safe_filename(library_filename, native_library_suffixes())
                library_bytes = _decode_base64_file(library_content_base64)
                if not library_bytes:
                    raise ValueError("原生库文件为空")
                native_info["library_filename"] = normalized_library_name
                native_info["size_bytes"] = len(library_bytes)
                suffix = Path(normalized_library_name).suffix
                with tempfile.TemporaryDirectory(prefix="aas-native-preview-") as tmp:
                    path = Path(tmp) / ("plugin" + suffix)
                    path.write_bytes(library_bytes)
                    from core.native_build import validate_native_library_isolated
                    native_info.update(validate_native_library_isolated(self.root, path, ptype))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(f"原生库校验失败: {type(exc).__name__}: {exc}")

        registry_name = REGISTRY_ATTRS.get(ptype, "")
        try:
            module = importlib.import_module(PLUGIN_TYPE_MAP[ptype].module)
            registry = getattr(module, registry_name, {})
            if name and isinstance(registry, dict) and name in registry:
                warnings.append("检测到同名运行期插件；安装将作为更新处理，并保留失败回滚能力。")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        summary = {
            "name": name,
            "label": descriptor.get("label", name),
            "language": language,
            "abi": descriptor.get("abi"),
            "library": descriptor.get("library"),
            "dependencies": dependency_names,
            "config_keys": sorted((descriptor.get("config") or {}).keys())
                if isinstance(descriptor.get("config"), dict) else [],
            "yaml_filename": normalized_yaml_name,
            "library_filename": normalized_library_name,
            "source_filename": normalized_source_name,
        }
        return {
            "status": "success",
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "descriptor": summary,
            "dependencies_status": dep_status,
            "code": {"provided": False},
            "native": native_info,
            "contribution_point": PLUGIN_TYPE_MAP[ptype].contribution_point,
        }

    def _install_native(
        self,
        ptype: str,
        yaml_filename: str,
        yaml_content: str,
        *,
        language: str,
        library_filename: str | None,
        library_content_base64: str | None,
        source_filename: str | None,
        source_content: str | None,
    ) -> dict[str, Any]:
        spec = PLUGIN_TYPE_MAP.get(ptype)
        if spec is None or not spec.installable or spec.discover_func is None:
            raise ValueError(f"不支持的插件类型: {ptype}")
        if ptype not in NATIVE_PLUGIN_TYPES:
            raise ValueError("C/C++ 原生插件首期仅支持检测算法和评估指标")

        yaml_filename = _safe_filename(yaml_filename, (".yaml", ".yml"))
        descriptor = yaml.safe_load(yaml_content or "")
        if not isinstance(descriptor, dict):
            raise ValueError("YAML 内容必须是对象")
        name = str(descriptor.get("name") or "").strip()
        if not name:
            raise ValueError("YAML 必须声明 name")
        if name in BUILTIN_PLUGIN_NAMES.get(ptype, set()):
            raise ValueError("内置插件名称受系统保护，不能覆盖")
        descriptor_language = normalize_native_language(descriptor.get("language"))
        if descriptor_language != language:
            raise ValueError(
                f"实现语言选择为 {language}，但 YAML language={descriptor.get('language')!r}；请保持一致"
            )
        contract_errors = native_descriptor_contract_errors(ptype, descriptor)
        if contract_errors:
            raise ValueError("；".join(contract_errors))
        if not (library_filename and library_content_base64):
            raise ValueError("安装 C/C++ 插件必须提供已编译的 DLL/SO 原生库")

        normalized_library_name = _safe_filename(library_filename, native_library_suffixes())
        library_bytes = _decode_base64_file(library_content_base64)
        if not library_bytes:
            raise ValueError("原生库文件为空")
        normalized_source_name: str | None = None
        if source_filename or source_content:
            if not (source_filename and source_content):
                raise ValueError("C/C++ 源文件名与源码内容必须同时提供")
            suffixes = (".c",) if language == "c" else (".cpp", ".cc", ".cxx")
            normalized_source_name = _safe_filename(source_filename, suffixes)

        # Validate the binary before touching the project tree.
        suffix = Path(normalized_library_name).suffix
        with tempfile.TemporaryDirectory(prefix="aas-native-install-") as tmp:
            temp_library = Path(tmp) / ("plugin" + suffix)
            temp_library.write_bytes(library_bytes)
            from core.native_build import validate_native_library_isolated
            native_contract = validate_native_library_isolated(self.root, temp_library, ptype)

        target_dir = self.root / spec.config_dir
        artifact_dir = self.root / "native_plugins" / ptype / _safe_native_dir_name(name)
        yaml_path = target_dir / yaml_filename
        library_path = artifact_dir / normalized_library_name
        source_path = artifact_dir / normalized_source_name if normalized_source_name else None
        existing_descriptor = _find_descriptor(self.root, spec, ptype, name)
        if existing_descriptor is not None and existing_descriptor[0] != yaml_path:
            raise ValueError("已有同名插件使用其他描述文件，拒绝重复安装")
        self._check_install_target(ptype, name, yaml_path, yaml_path, "yaml")
        self._check_install_target(ptype, name, yaml_path, library_path, "library")
        if source_path is not None:
            self._check_install_target(ptype, name, yaml_path, source_path, "source")
        target_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # The installed descriptor points at the managed artifact, not at the
        # user's temporary upload path or template filename.
        installed_descriptor = dict(descriptor)
        installed_descriptor["language"] = language
        installed_descriptor["abi"] = NATIVE_ABI_V1
        installed_descriptor["library"] = library_path.relative_to(self.root).as_posix()
        if source_path is not None:
            installed_descriptor["source"] = source_path.relative_to(self.root).as_posix()
        else:
            installed_descriptor.pop("source", None)
        installed_yaml = yaml.safe_dump(installed_descriptor, allow_unicode=True, sort_keys=False)

        backups: dict[Path, bytes | None] = {}
        for path in (library_path, source_path, yaml_path):
            if path is not None:
                backups[path] = path.read_bytes() if path.exists() else None

        def rollback() -> None:
            for path, previous in backups.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(previous)

        module = importlib.import_module(spec.module)
        discover = getattr(module, spec.discover_func)
        registry = getattr(module, REGISTRY_ATTRS.get(ptype, ""), {})
        registry_before = dict(registry) if isinstance(registry, dict) else None
        detector_meta = getattr(module, "DETECTOR_METADATA", None) if ptype == "detectors" else None
        detector_meta_before = dict(detector_meta) if isinstance(detector_meta, dict) else None
        written: list[str] = []
        try:
            library_path.write_bytes(library_bytes)
            written.append(str(library_path))
            if source_path is not None:
                source_path.write_bytes((source_content or "").encode("utf-8"))
                written.append(str(source_path))
            yaml_path.write_bytes(installed_yaml.encode("utf-8"))
            written.append(str(yaml_path))

            discovered_count = int(discover(target_dir) or 0)
            if ptype == "metrics":
                obj = registry.get(name) if isinstance(registry, dict) else None
                registered = obj is not None
                health = _validate_object(ptype, obj) if obj is not None else {
                    "healthy": False, "missing_contract": ["registry_entry"]
                }
            else:
                from detectors.registry import get_available_algorithms, validate_detector_descriptor
                registered = any(str(item.get("name")) == name for item in get_available_algorithms())
                validate_detector_descriptor(
                    {**installed_descriptor, "_descriptor_path": str(yaml_path)},
                    target_dir,
                    force_reload=True,
                )
                health = {"healthy": registered, "missing_contract": [] if registered else ["catalog_entry"]}

            if not registered or not health.get("healthy"):
                reason = ", ".join(health.get("missing_contract", [])) or "未进入运行期注册表"
                raise ValueError(f"插件安装验证失败: {reason}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Close a newly registered native metric before restoring/deleting
            # the DLL, especially important on Windows where loaded DLL files are locked.
            if ptype == "metrics" and isinstance(registry, dict):
                current = registry.get(name)
                if current is not None and hasattr(current, "close"):
                    try:
                        current.close()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
            rollback()
            if isinstance(registry, dict) and registry_before is not None:
                registry.clear()
                registry.update(registry_before)
            if isinstance(detector_meta, dict) and detector_meta_before is not None:
                detector_meta.clear()
                detector_meta.update(detector_meta_before)
            try:
                discover(target_dir)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            return {
                "status": "success",
                "registered": False,
                "healthy": False,
                "contract": {"healthy": False, "missing_contract": [str(exc)]},
                "native_contract": native_contract,
                "discovered_count": 0,
                "discovery_error": f"{type(exc).__name__}: {exc}",
                "plugin": name,
                "language": language,
                "contribution_point": spec.contribution_point,
                "activation_events": list(spec.activation_events),
                "written": [],
                "rolled_back": True,
            }

        manifest_error = ""
        try:
            self._record_installation(
                ptype, name, yaml_path,
                library_path=library_path,
                source_path=source_path,
                language=language,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            manifest_error = f"{type(exc).__name__}: {exc}"

        return {
            "status": "success",
            "registered": True,
            "healthy": True,
            "contract": health,
            "native_contract": native_contract,
            "discovered_count": discovered_count,
            "discovery_error": "",
            "plugin": name,
            "language": language,
            "contribution_point": spec.contribution_point,
            "activation_events": list(spec.activation_events),
            "written": written,
            "manifest_error": manifest_error,
        }

    def preview(
        self,
        ptype: str,
        yaml_filename: str,
        yaml_content: str,
        code_filename: str | None = None,
        code_content: str | None = None,
        *,
        language: str | None = None,
        library_filename: str | None = None,
        library_content_base64: str | None = None,
        source_filename: str | None = None,
        source_content: str | None = None,
    ) -> dict[str, Any]:
        """Parse and validate a plugin without writing anything to disk.

        The preview checks descriptor structure, source syntax, class/base-class
        contract and dependency availability.  It is intentionally side-effect
        free with respect to the project tree so the UI can validate imported
        files before the user chooses to install them.
        """
        try:
            preview_raw = yaml.safe_load(yaml_content or "")
        except yaml.YAMLError:
            preview_raw = None
        selected_language = normalize_native_language(
            language or ((preview_raw or {}).get("language") if isinstance(preview_raw, dict) else "python")
        )
        if selected_language in {"c", "cpp"}:
            return self._preview_native(
                ptype, yaml_filename, yaml_content,
                language=selected_language,
                library_filename=library_filename,
                library_content_base64=library_content_base64,
                source_filename=source_filename,
                source_content=source_content,
            )

        errors: list[str] = []
        warnings: list[str] = []
        descriptor: dict[str, Any] = {}
        code_info: dict[str, Any] = {"provided": bool(code_filename or code_content)}
        dependency_names: list[str] = []
        dep_status = {"installed": True, "missing": []}

        spec = PLUGIN_TYPE_MAP.get(ptype)
        if spec is None or not spec.installable or spec.discover_func is None:
            return {
                "status": "success", "valid": False,
                "errors": [f"不支持的插件类型: {ptype}"], "warnings": [],
            }

        try:
            normalized_yaml_name = _safe_filename(yaml_filename, (".yaml", ".yml"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            normalized_yaml_name = str(yaml_filename or "")
            errors.append(str(exc))

        try:
            raw = yaml.safe_load(yaml_content or "")
            if not isinstance(raw, dict):
                raise ValueError("YAML 内容必须是对象")
            descriptor = dict(raw)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(f"YAML 解析失败: {exc}")

        name = str(descriptor.get("name") or "").strip()
        class_path = str(descriptor.get("class_path") or "").strip()
        if descriptor:
            if not name:
                errors.append("YAML 必须声明 name")
            if ptype == "fault_types" and name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
                errors.append("故障类型 name 只能包含字母、数字和下划线，且不能以数字开头")
            if ptype == "fault_types" and name in BUILTIN_PLUGIN_NAMES.get("fault_types", set()):
                errors.append("内置故障类型名称受系统保护，请使用新的自定义 name")
            if not class_path or "." not in class_path:
                errors.append("YAML 必须声明合法的 class_path（module.ClassName）")
            if ptype == "datasources":
                formats = descriptor.get("formats")
                if not isinstance(formats, list) or not [x for x in formats if str(x).strip()]:
                    errors.append("数据源插件必须声明非空 formats 列表")
            if "config" in descriptor and not isinstance(descriptor.get("config"), dict):
                errors.append("config 必须是对象")
            if "config_schema" in descriptor and not isinstance(descriptor.get("config_schema"), list):
                errors.append("config_schema 必须是列表")
            try:
                dependency_names = _normalize_dependency_names(descriptor.get("dependencies", []))
                dep_status = _check_deps(dependency_names)
                if dep_status["missing"]:
                    warnings.append("当前环境缺少依赖: " + ", ".join(dep_status["missing"]))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(str(exc))

        cls: type[Any] | None = None
        normalized_code_name: str | None = None
        if code_filename or code_content:
            if not (code_filename and code_content):
                errors.append("代码文件名与代码内容必须同时提供")
            else:
                try:
                    normalized_code_name = _safe_filename(code_filename, (".py",))
                    code_info["filename"] = normalized_code_name
                    if class_path and "." in class_path:
                        module_name, _class_name = class_path.rsplit(".", 1)
                        code_stem = Path(normalized_code_name).stem
                        if module_name != code_stem and not module_name.endswith(f".{code_stem}"):
                            errors.append(
                                f"class_path 模块 {module_name!r} 与代码文件 {normalized_code_name!r} 不匹配"
                            )
                        else:
                            code_info.update(
                                _static_validate_plugin_source(
                                    ptype, class_path, code_content, normalized_code_name
                                )
                            )
                except SyntaxError as exc:
                    code_info["syntax_ok"] = False
                    errors.append(f"Python 语法错误: 第 {exc.lineno} 行 {exc.msg}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    code_info["syntax_ok"] = False
                    errors.append(f"代码加载失败: {type(exc).__name__}: {exc}")
        elif class_path and "." in class_path and not errors:
            try:
                from core.plugin_loader import import_plugin_class

                search_roots = [self.root, self.root / spec.config_dir]
                if spec.code_dir:
                    search_roots.append(self.root / spec.code_dir)
                cls = import_plugin_class(class_path, search_roots)
                code_info.update({"provided": False, "resolved_existing_class": True})
                warnings.append("未上传 Python 源码；本次安装将复用当前环境中已有的 class_path 实现。")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    "未提供代码文件，且 class_path 在当前环境中无法解析: "
                    f"{type(exc).__name__}: {exc}"
                )

        if cls is not None:
            try:
                base_cls = _plugin_base_class(ptype)
                code_info["class_name"] = cls.__name__
                code_info["base_class"] = f"{base_cls.__module__}.{base_cls.__name__}"
                if not issubclass(cls, base_cls):
                    errors.append(f"插件类必须继承 {base_cls.__module__}.{base_cls.__name__}")
                abstract = sorted(getattr(cls, "__abstractmethods__", set()))
                code_info["abstract_methods"] = abstract
                if abstract:
                    errors.append("仍有未实现的抽象方法: " + ", ".join(abstract))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(f"插件契约校验失败: {type(exc).__name__}: {exc}")

        registry_name = REGISTRY_ATTRS.get(ptype, "")
        try:
            module = importlib.import_module(spec.module)
            registry = getattr(module, registry_name, {})
            if name and isinstance(registry, dict) and name in registry:
                warnings.append("检测到同名运行期插件；安装将作为更新处理，并保留失败回滚能力。")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        summary = {
            "name": name,
            "label": descriptor.get("label", name),
            "class_path": class_path,
            "type": descriptor.get("type"),
            "formats": descriptor.get("formats", []),
            "dependencies": dependency_names,
            "config_keys": sorted((descriptor.get("config") or {}).keys())
                if isinstance(descriptor.get("config"), dict) else [],
            "yaml_filename": normalized_yaml_name,
            "code_filename": normalized_code_name,
        }
        return {
            "status": "success",
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "descriptor": summary,
            "dependencies_status": dep_status,
            "code": code_info,
            "contribution_point": spec.contribution_point,
        }

    def install(
        self,
        ptype: str,
        yaml_filename: str,
        yaml_content: str,
        code_filename: str | None = None,
        code_content: str | None = None,
        *,
        language: str | None = None,
        library_filename: str | None = None,
        library_content_base64: str | None = None,
        source_filename: str | None = None,
        source_content: str | None = None,
    ) -> dict[str, Any]:
        """Install a plugin descriptor and optional implementation atomically.

        Invalid updates are rolled back on disk and the previous descriptor is
        rediscovered, so a bad upload cannot replace a working plugin.
        """
        try:
            install_raw = yaml.safe_load(yaml_content or "")
        except yaml.YAMLError:
            install_raw = None
        selected_language = normalize_native_language(
            language or ((install_raw or {}).get("language") if isinstance(install_raw, dict) else "python")
        )
        if selected_language in {"c", "cpp"}:
            return self._install_native(
                ptype, yaml_filename, yaml_content,
                language=selected_language,
                library_filename=library_filename,
                library_content_base64=library_content_base64,
                source_filename=source_filename,
                source_content=source_content,
            )

        spec = PLUGIN_TYPE_MAP.get(ptype)
        if spec is None or not spec.installable or spec.discover_func is None:
            raise ValueError(f"不支持的插件类型: {ptype}")

        yaml_filename = _safe_filename(yaml_filename, (".yaml", ".yml"))
        descriptor = yaml.safe_load(yaml_content or "")
        if not isinstance(descriptor, dict):
            raise ValueError("YAML 内容必须是对象")
        if not str(descriptor.get("name") or "").strip():
            raise ValueError("YAML 必须声明 name")
        if str(descriptor.get("name") or "").strip() in BUILTIN_PLUGIN_NAMES.get(ptype, set()):
            raise ValueError("内置插件名称受系统保护，不能覆盖")
        if ptype == "fault_types" and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(descriptor.get("name") or "").strip()
        ):
            raise ValueError("故障类型 name 只能包含字母、数字和下划线，且不能以数字开头")
        if ptype == "fault_types" and str(descriptor.get("name") or "").strip() in BUILTIN_PLUGIN_NAMES.get("fault_types", set()):
            raise ValueError("内置故障类型名称受系统保护，请使用新的自定义 name")
        class_path = str(descriptor.get("class_path") or "").strip()
        if not class_path:
            raise ValueError("YAML 必须声明 class_path")

        normalized_code_name: str | None = None
        if code_filename or code_content:
            if not (code_filename and code_content):
                raise ValueError("代码文件名与代码内容必须同时提供")
            normalized_code_name = _safe_filename(code_filename, (".py",))
            module_name = class_path.rsplit(".", 1)[0]
            code_stem = Path(normalized_code_name).stem
            if module_name != code_stem and not module_name.endswith(f".{code_stem}"):
                raise ValueError(
                    f"class_path 模块 {module_name!r} 与代码文件 {normalized_code_name!r} 不匹配"
                )
            # Compile before touching the installed copy.
            compile(code_content, normalized_code_name, "exec")

        target_dir = self.root / spec.config_dir
        code_dir = self.root / (spec.code_dir or spec.config_dir)
        yaml_path = target_dir / yaml_filename
        code_path = code_dir / normalized_code_name if normalized_code_name else None
        name = str(descriptor["name"]).strip()
        existing_descriptor = _find_descriptor(self.root, spec, ptype, name)
        if existing_descriptor is not None and existing_descriptor[0] != yaml_path:
            raise ValueError("已有同名插件使用其他描述文件，拒绝重复安装")
        self._check_install_target(ptype, name, yaml_path, yaml_path, "yaml")
        if code_path is not None:
            self._check_install_target(ptype, name, yaml_path, code_path, "code")
            if self._code_referenced_elsewhere(spec, yaml_path, class_path):
                raise ValueError("实现模块被其他描述文件引用，拒绝覆盖")
        target_dir.mkdir(parents=True, exist_ok=True)
        code_dir.mkdir(parents=True, exist_ok=True)

        backups: dict[Path, bytes | None] = {}
        for path in (code_path, yaml_path):
            if path is not None:
                backups[path] = path.read_bytes() if path.exists() else None

        def rollback() -> None:
            for path, previous in backups.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)

        written: list[str] = []
        module = importlib.import_module(spec.module)
        discover = getattr(module, spec.discover_func)
        registry = getattr(module, REGISTRY_ATTRS.get(ptype, ""), {})
        registry_before = dict(registry) if isinstance(registry, dict) else None
        detector_meta = getattr(module, "DETECTOR_METADATA", None) if ptype == "detectors" else None
        detector_meta_before = dict(detector_meta) if isinstance(detector_meta, dict) else None
        try:
            # Implementation first, descriptor last: discovery never observes a
            # new descriptor before its referenced implementation exists.
            if code_path is not None:
                code_path.write_bytes((code_content or "").encode("utf-8"))
                written.append(str(code_path))
            yaml_path.write_bytes((yaml_content or "").encode("utf-8"))
            written.append(str(yaml_path))

            discovered_count = int(discover(target_dir) or 0)
            expected = str(descriptor["name"]).strip()
            registered_names = {str(name) for name in registry.keys()} if isinstance(registry, dict) else set()
            registered = expected in registered_names
            registry_key = expected
            if ptype == "datasources" and not registered:
                formats = [str(fmt) for fmt in descriptor.get("formats", []) or []]
                registered = any(fmt in registered_names for fmt in formats)
                registry_key = next((fmt for fmt in formats if fmt in registered_names), expected)

            obj = registry.get(registry_key) if isinstance(registry, dict) and registry_key else None
            if ptype == "detectors" and registered:
                validator = getattr(module, "validate_detector_descriptor")
                validator(descriptor, target_dir, force_reload=True)
                health = {"healthy": True, "missing_contract": []}
            else:
                health = (
                    _validate_object(ptype, obj)
                    if obj is not None
                    else {"healthy": False, "missing_contract": ["registry_entry"]}
                )

            if not registered or not health["healthy"]:
                reason = ", ".join(health.get("missing_contract", [])) or "未进入运行期注册表"
                raise ValueError(f"插件安装验证失败: {reason}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            rollback()
            if isinstance(registry, dict) and registry_before is not None:
                registry.clear()
                registry.update(registry_before)
            if isinstance(detector_meta, dict) and detector_meta_before is not None:
                detector_meta.clear()
                detector_meta.update(detector_meta_before)
            # Re-discover the restored descriptor so an updated plugin resumes
            # with its previous implementation immediately.
            try:
                discover(target_dir)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            return {
                "status": "success",
                "registered": False,
                "healthy": False,
                "contract": {"healthy": False, "missing_contract": [str(exc)]},
                "discovered_count": 0,
                "discovery_error": f"{type(exc).__name__}: {exc}",
                "plugin": str(descriptor["name"]).strip(),
                "contribution_point": spec.contribution_point,
                "activation_events": list(spec.activation_events),
                "written": [],
                "rolled_back": True,
            }

        manifest_error = ""
        try:
            self._record_installation(
                ptype,
                str(descriptor["name"]).strip(),
                yaml_path,
                code_path,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # The plugin itself is healthy and already registered.  A manifest
            # write failure only means a future uninstall will conservatively
            # retain the Python implementation instead of deleting it.
            manifest_error = f"{type(exc).__name__}: {exc}"

        return {
            "status": "success",
            "registered": True,
            "healthy": True,
            "contract": health,
            "discovered_count": discovered_count,
            "discovery_error": "",
            "plugin": str(descriptor["name"]).strip(),
            "contribution_point": spec.contribution_point,
            "activation_events": list(spec.activation_events),
            "written": written,
            "manifest_error": manifest_error,
        }

    def uninstall(self, ptype: str, plugin_name: str, *, force: bool = False) -> dict[str, Any]:
        """Preview or uninstall one user-provided plugin.

        ``force=False`` is a side-effect-free preflight used by the browser to
        build a confirmation message.  ``force=True`` removes the descriptor,
        unregisters the plugin immediately, and removes Python code only when
        that code file was recorded as managed by the installer and no other
        descriptor references the same module.
        """
        spec = PLUGIN_TYPE_MAP.get(ptype)
        if spec is None or not spec.installable or spec.discover_func is None:
            raise ValueError(f"不支持的插件类型: {ptype}")
        name = str(plugin_name or "").strip()
        if not name:
            raise ValueError("插件名称不能为空")
        if name in BUILTIN_PLUGIN_NAMES.get(ptype, set()):
            raise ValueError("内置插件受系统保护，不能卸载")

        descriptor_record = _find_descriptor(self.root, spec, ptype, name)
        if descriptor_record is None:
            raise FileNotFoundError(f"未找到可卸载的自定义插件描述: {name}")
        descriptor_path, descriptor = descriptor_record
        descriptor_name = str(descriptor.get("name") or "").strip()
        if not descriptor_name:
            raise ValueError("插件描述缺少 name，无法安全卸载")
        if descriptor_name in BUILTIN_PLUGIN_NAMES.get(ptype, set()):
            raise ValueError("内置插件受系统保护，不能卸载")

        references = self._config_references(ptype, descriptor, descriptor_path)
        manifest = self._load_manifest()
        manifest_entry = manifest.get("plugins", {}).get(self._manifest_key(ptype, descriptor_name)) or {}
        # Never unlink an imported link or an on-disk built-in descriptor.
        expected_config_dir = self.root / spec.config_dir
        if (descriptor_path.parent != expected_config_dir
                or not self._path_is_unlinked_inside_root(descriptor_path)
                or descriptor_path.stem in BUILTIN_PLUGIN_NAMES.get(ptype, set())):
            raise ValueError("插件描述文件不属于可卸载的自定义目录")
        manifest_entry = manifest.get("plugins", {}).get(self._manifest_key(ptype, descriptor_name)) or {}
        if not isinstance(manifest_entry, dict):
            manifest_entry = {}
        managed_code_rel, managed_code_path = self._manifest_owned_path(
            spec, descriptor_name, descriptor_path, descriptor, manifest_entry, "code"
        )
        managed_library_rel, managed_library_path = self._manifest_owned_path(
            spec, descriptor_name, descriptor_path, descriptor, manifest_entry, "library"
        )
        managed_source_rel, managed_source_path = self._manifest_owned_path(
            spec, descriptor_name, descriptor_path, descriptor, manifest_entry, "source"
        )

        code_will_remove = False
        class_path = str(descriptor.get("class_path") or "").strip()
        if managed_code_path is not None and managed_code_path.is_file():
            code_will_remove = (
                not self._code_referenced_elsewhere(spec, descriptor_path, class_path)
                and not self._referenced_in_other_manifests(manifest, ptype, descriptor_name, managed_code_rel)
            )

        language = normalize_native_language(descriptor.get("language"))
        native = language in {"c", "cpp"}
        library_will_remove = bool(
            native and managed_library_path is not None and managed_library_path.is_file()
            and not self._descriptor_artifact_referenced_elsewhere(
                spec, descriptor_path, "library", str(descriptor.get("library") or "")
            )
            and not self._referenced_in_other_manifests(manifest, ptype, descriptor_name, managed_library_rel)
        )
        source_will_remove = bool(
            native and managed_source_path is not None and managed_source_path.is_file()
            and not self._descriptor_artifact_referenced_elsewhere(
                spec, descriptor_path, "source", str(descriptor.get("source") or "")
            )
            and not self._referenced_in_other_manifests(manifest, ptype, descriptor_name, managed_source_rel)
        )

        ownership_warnings = [
            f"{field}: 清单路径不属于当前插件，保留文件并禁止删除"
            for field, rel, path in (
                ("code", managed_code_rel, managed_code_path),
                ("library", managed_library_rel, managed_library_path),
                ("source", managed_source_rel, managed_source_path),
            ) if rel and path is None
        ]
        preview = {
            "status": "success",
            "plugin": descriptor_name,
            "type": ptype,
            "label": descriptor.get("label", descriptor_name),
            "language": language,
            "uninstallable": True,
            "requires_confirmation": True,
            "references": references,
            "descriptor": descriptor_path.relative_to(self.root).as_posix(),
            "managed_code": managed_code_rel,
            "managed_library": managed_library_rel,
            "managed_source": managed_source_rel,
            "code_will_remove": code_will_remove,
            "library_will_remove": library_will_remove,
            "source_will_remove": source_will_remove,
            "ownership_warnings": ownership_warnings,
            "uninstalled": False,
        }
        if not force:
            return preview

        module = importlib.import_module(spec.module)
        registry = getattr(module, REGISTRY_ATTRS.get(ptype, ""), {})
        registry_before = dict(registry) if isinstance(registry, dict) else None
        detector_meta = getattr(module, "DETECTOR_METADATA", None) if ptype == "detectors" else None
        detector_meta_before = dict(detector_meta) if isinstance(detector_meta, dict) else None
        manifest_bytes = self._manifest_path.read_bytes() if self._manifest_path.exists() else None
        descriptor_bytes = descriptor_path.read_bytes()
        code_bytes = (
            managed_code_path.read_bytes()
            if code_will_remove and managed_code_path is not None and managed_code_path.exists()
            else None
        )
        library_bytes = (
            managed_library_path.read_bytes()
            if library_will_remove and managed_library_path is not None and managed_library_path.exists()
            else None
        )
        source_bytes = (
            managed_source_path.read_bytes()
            if source_will_remove and managed_source_path is not None and managed_source_path.exists()
            else None
        )

        removed_files: list[str] = []
        retained_files: list[str] = []
        try:
            # Remove on-disk discovery entry first, then unregister the current
            # process so the capability disappears without a service restart.
            descriptor_path.unlink()
            removed_files.append(descriptor_path.relative_to(self.root).as_posix())

            if isinstance(registry, dict):
                if ptype == "datasources":
                    formats = {str(fmt).strip() for fmt in descriptor.get("formats", []) or []}
                    for key, obj in list(registry.items()):
                        obj_name = str(getattr(obj, "name", "") or "").strip()
                        if str(key) in formats or obj_name == descriptor_name:
                            registry.pop(key, None)
                else:
                    current = registry.get(descriptor_name)
                    if current is not None and hasattr(current, "close"):
                        try:
                            current.close()
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass
                    registry.pop(descriptor_name, None)
                    if name != descriptor_name:
                        registry.pop(name, None)

            if isinstance(detector_meta, dict):
                detector_meta.pop(descriptor_name, None)

            if managed_code_path is not None and managed_code_path.exists():
                rel = managed_code_path.relative_to(self.root.resolve()).as_posix()
                if code_will_remove:
                    managed_code_path.unlink()
                    removed_files.append(rel)
                else:
                    retained_files.append(rel)

            for path, should_remove in (
                (managed_library_path, library_will_remove),
                (managed_source_path, source_will_remove),
            ):
                if path is None or not path.exists():
                    continue
                rel = path.relative_to(self.root.resolve()).as_posix()
                if should_remove:
                    path.unlink()
                    removed_files.append(rel)
                else:
                    retained_files.append(rel)

            # Native artifacts are stored in one managed directory per plugin.
            # Remove that directory when uninstall leaves it empty so repeated
            # install/uninstall cycles do not accumulate empty folders.
            native_root = (self.root / "native_plugins").resolve()
            native_type_root = (native_root / ptype).resolve()
            for artifact_path in (managed_library_path, managed_source_path):
                if artifact_path is None:
                    continue
                parent = artifact_path.parent.resolve()
                if parent == native_type_root or native_type_root not in parent.parents:
                    continue
                try:
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass

            data = self._load_manifest()
            data.get("plugins", {}).pop(self._manifest_key(ptype, descriptor_name), None)
            self._save_manifest(data)

            # Evict the custom implementation module so reinstalling a file with
            # the same module name in this process cannot reuse stale code.
            module_name = class_path.rsplit(".", 1)[0] if "." in class_path else ""
            if code_will_remove and module_name:
                sys.modules.pop(module_name, None)
        except Exception:
            descriptor_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor_path.write_bytes(descriptor_bytes)
            if code_will_remove and managed_code_path is not None and code_bytes is not None:
                managed_code_path.parent.mkdir(parents=True, exist_ok=True)
                managed_code_path.write_bytes(code_bytes)
            if library_will_remove and managed_library_path is not None and library_bytes is not None:
                managed_library_path.parent.mkdir(parents=True, exist_ok=True)
                managed_library_path.write_bytes(library_bytes)
            if source_will_remove and managed_source_path is not None and source_bytes is not None:
                managed_source_path.parent.mkdir(parents=True, exist_ok=True)
                managed_source_path.write_bytes(source_bytes)
            if isinstance(registry, dict) and registry_before is not None:
                registry.clear()
                registry.update(registry_before)
            if isinstance(detector_meta, dict) and detector_meta_before is not None:
                detector_meta.clear()
                detector_meta.update(detector_meta_before)
            if manifest_bytes is None:
                self._manifest_path.unlink(missing_ok=True)
            else:
                self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
                self._manifest_path.write_bytes(manifest_bytes)
            try:
                getattr(module, spec.discover_func)(descriptor_path.parent)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            raise

        preview.update({
            "requires_confirmation": False,
            "uninstalled": True,
            "removed_files": removed_files,
            "retained_files": retained_files,
        })
        return preview
