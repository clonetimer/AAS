"""Phase 34: cohesive domain services, split from the original services.py.

Import through ``scripts.server.services`` for compatibility, or use this module
directly for new code. No database schema or API change is introduced.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403 - central paths and SQLite guard


def _algorithm_project_root(project_root=None) -> Path:
    """Use an explicit host root or the normal services compatibility root."""
    return Path(project_root) if project_root is not None else Path(_service_setting("PROJECT_ROOT"))


def preview_algorithm(file_content: str) -> dict[str, Any]:
    """预览算法配置"""
    import yaml

    try:
        config = yaml.safe_load(file_content)
    except yaml.YAMLError:
        config = json.loads(file_content)

    if not isinstance(config, dict):
        raise ValueError("Invalid algorithm configuration format")

    missing_fields = [field for field in ['name', 'class_path'] if field not in config]
    return {
        "config": config,
        "valid": not missing_fields,
        "missing_fields": missing_fields,
    }


def validate_detector_interface(config: dict) -> dict[str, Any]:
    """Validate detector imports against the canonical BaseDetector contract."""
    from detectors.registry import validate_detector_descriptor

    try:
        return validate_detector_descriptor(config, force_reload=True)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"检测器接口验证失败：{exc}\n"
            "检测器必须继承 detectors.base.BaseDetector，并声明唯一 decision_mode。\n"
            "score_threshold 实现 fit()+score()；native 实现 native_predict()；label 实现 label_predict()。\n"
            "请参考 configs/detectors/template_detector.py"
        ) from exc

def import_algorithm(file_content: bytes, filename: str, mode: str, *, project_root=None) -> dict[str, Any]:
    """
    导入算法配置或代码

    Args:
        file_content: 文件内容（bytes）
        filename: 文件名
        mode: 导入模式
            - 'config_only': 仅配置文件
            - 'zip': ZIP 包（代码 + 配置）
            - 'code_only': 仅代码文件

    Returns:
        dict: 导入结果
    """
    detectors_dir = _algorithm_project_root(project_root) / "configs" / "detectors"
    detectors_dir.mkdir(parents=True, exist_ok=True)

    if mode == 'zip':
        return import_from_zip(file_content, filename, detectors_dir, project_root=project_root)
    elif mode == 'code_only':
        return import_code_only(file_content, filename, detectors_dir, project_root=project_root)
    elif mode == 'config_only':
        return import_config_only(file_content, filename, detectors_dir, project_root=project_root)
    else:
        raise ValueError(f"Unknown import mode: {mode}")


def import_from_zip(zip_content: bytes, filename: str, detectors_dir: Path, *, project_root=None) -> dict[str, Any]:
    """Import one detector package through the unified PluginRuntime."""
    del filename, detectors_dir
    import zipfile
    from io import BytesIO

    from core.plugin_runtime import PluginRuntime

    project_root = _algorithm_project_root(project_root)
    try:
        with zipfile.ZipFile(BytesIO(zip_content)) as zip_file:
            members = [m for m in zip_file.namelist() if not m.endswith("/")]
            python_files = [m for m in members if m.endswith(".py") and Path(m).name == m]
            yaml_files = [m for m in members if m.endswith((".yaml", ".yml")) and Path(m).name == m]
            if len(python_files) != 1 or len(yaml_files) != 1:
                raise ValueError("ZIP 包必须在根目录包含且只包含一个 .py 和一个 .yaml/.yml 插件文件")

            code_name = Path(python_files[0]).name
            yaml_name = Path(yaml_files[0]).name
            try:
                code_content = zip_file.read(python_files[0]).decode("utf-8")
                yaml_content = zip_file.read(yaml_files[0]).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("插件代码和 YAML 必须使用 UTF-8 编码") from exc

            result = PluginRuntime(project_root).install(
                "detectors", yaml_name, yaml_content, code_name, code_content
            )
            if not result.get("registered"):
                reason = result.get("discovery_error") or result.get("contract", {}).get("missing_contract")
                raise ValueError(f"检测算法未进入运行期注册表：{reason or '请检查插件契约和依赖'}")
            return {
                "status": "success",
                "registered": True,
                "code_path": (project_root / "detectors" / code_name).relative_to(project_root).as_posix(),
                "config_path": (project_root / "configs" / "detectors" / yaml_name).relative_to(project_root).as_posix(),
                "files": {"python": [code_name], "yaml": [yaml_name]},
                "plugin": result.get("plugin"),
            }
    except zipfile.BadZipFile as exc:
        raise ValueError("无效的 ZIP 文件格式") from exc

def import_code_only(code_content: bytes, filename: str, detectors_dir: Path, *, project_root=None) -> dict[str, Any]:
    """Save detector source safely; the YAML class_path drives contract validation."""
    del detectors_dir
    project_root = _algorithm_project_root(project_root)
    raw_name = (filename or "").strip()
    candidate = Path(raw_name)
    if not raw_name or candidate.name != raw_name or candidate.suffix.lower() != ".py":
        raise ValueError("代码文件必须是根目录下的 .py 文件，不能包含路径")
    try:
        source = code_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("检测算法代码必须使用 UTF-8 编码") from exc
    compile(source, raw_name, "exec")

    dest_path = project_root / "detectors" / raw_name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(source.encode("utf-8"))
    return {
        "code_path": dest_path.relative_to(project_root).as_posix(),
        "code_name": candidate.stem,
        "validation": {
            "valid": True,
            "message": "代码语法检查通过；提供 YAML 后按 BaseDetector 契约完成注册验证",
        },
    }


def import_config_only(file_content: str | bytes, filename: str, detectors_dir: Path, *, project_root=None) -> dict[str, Any]:
    """Install a detector descriptor for an implementation already on disk."""
    del filename, detectors_dir
    import yaml
    from core.plugin_runtime import PluginRuntime

    project_root = _algorithm_project_root(project_root)
    try:
        content = file_content.decode("utf-8") if isinstance(file_content, bytes) else str(file_content)
    except UnicodeDecodeError as exc:
        raise ValueError("检测算法 YAML 必须使用 UTF-8 编码") from exc
    config = yaml.safe_load(content or "")
    if not isinstance(config, dict):
        raise ValueError("配置文件格式错误")
    name = str(config.get("name") or "").strip()
    if not name:
        raise ValueError("配置缺少必填字段：name")

    config_name = f"{name}.yaml"
    installed = PluginRuntime(project_root).install("detectors", config_name, content)
    if not installed.get("registered"):
        reason = installed.get("discovery_error") or "; ".join(installed.get("contract", {}).get("missing_contract", []))
        raise ValueError(f"检测算法注册失败：{reason or '请检查 class_path、基类和依赖'}")
    return {
        "config_path": (project_root / "configs" / "detectors" / config_name).relative_to(project_root).as_posix(),
        "config_name": name,
        "registered": bool(installed.get("registered")),
        "validation": installed.get("contract", {"healthy": True, "missing_contract": []}),
    }


def import_algorithm_separate(
    config_file: bytes,
    config_filename: str,
    code_file: bytes,
    code_filename: str,
    *, project_root=None,
) -> dict[str, Any]:
    """Import detector source/config using the same runtime as the plugin center."""
    import yaml
    from core.plugin_runtime import PluginRuntime

    project_root = _algorithm_project_root(project_root)
    config_dir = project_root / "configs" / "detectors"
    code_dir = project_root / "detectors"
    config_dir.mkdir(parents=True, exist_ok=True)
    code_dir.mkdir(parents=True, exist_ok=True)

    def safe_name(raw_name: str, suffixes: tuple[str, ...]) -> str:
        raw_name = (raw_name or "").strip()
        candidate = Path(raw_name)
        if not raw_name or candidate.name != raw_name or candidate.suffix.lower() not in suffixes:
            raise ValueError("文件名不能包含路径，且扩展名不受支持")
        return raw_name

    def existing_file(directory: Path, raw_name: str, suffixes: tuple[str, ...]) -> Path:
        path = directory / safe_name(raw_name, suffixes)
        if not path.is_file():
            raise ValueError(f"文件不存在: {path.name}")
        return path

    code_text: str | None = None
    normalized_code_name: str | None = None
    if code_file:
        normalized_code_name = safe_name(code_filename, (".py",))
        try:
            code_text = code_file.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("检测算法代码必须使用 UTF-8 编码") from exc
        compile(code_text, normalized_code_name, "exec")
    elif code_filename:
        existing_code = existing_file(code_dir, code_filename, (".py",))
        normalized_code_name = existing_code.name
        code_text = existing_code.read_text(encoding="utf-8")
        compile(code_text, existing_code.name, "exec")

    config_text: str | None = None
    if config_file:
        safe_name(config_filename or "plugin.yaml", (".yaml", ".yml"))
        try:
            config_text = config_file.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("检测算法 YAML 必须使用 UTF-8 编码") from exc
    elif config_filename:
        config_text = existing_file(config_dir, config_filename, (".yaml", ".yml")).read_text(encoding="utf-8")

    if config_text is None:
        if code_text is None or normalized_code_name is None:
            raise ValueError("配置文件或代码文件至少需要一个")
        return {
            "config": None,
            "code": import_code_only(code_text.encode("utf-8"), normalized_code_name, config_dir, project_root=project_root),
            "registered": False,
            "message": "代码已保存，提供 YAML 后即可注册",
        }

    descriptor = yaml.safe_load(config_text or "")
    if not isinstance(descriptor, dict):
        raise ValueError("配置文件格式错误")
    name = str(descriptor.get("name") or "").strip()
    if not name:
        raise ValueError("配置缺少必填字段：name")
    yaml_name = f"{name}.yaml"

    if normalized_code_name:
        class_path = str(descriptor.get("class_path") or "").strip()
        module_name = class_path.rsplit(".", 1)[0] if "." in class_path else ""
        stem = Path(normalized_code_name).stem
        if module_name != stem and not module_name.endswith(f".{stem}"):
            raise ValueError(
                f"class_path 模块 {module_name!r} 与代码文件 {normalized_code_name!r} 不匹配"
            )

    runtime = PluginRuntime(project_root)
    if code_file and normalized_code_name and code_text is not None:
        installed = runtime.install(
            "detectors", yaml_name, config_text, normalized_code_name, code_text
        )
    else:
        installed = runtime.install("detectors", yaml_name, config_text)
    if not installed.get("registered"):
        reason = installed.get("discovery_error") or "; ".join(installed.get("contract", {}).get("missing_contract", []))
        raise ValueError(f"检测算法注册失败：{reason or '请检查 class_path、基类和依赖'}")

    result = {
        "config": {
            "path": (config_dir / yaml_name).relative_to(project_root).as_posix(),
            "name": name,
            "validation": installed.get("contract", {}),
        },
        "code": None,
        "registered": bool(installed.get("registered")),
        "message": "导入完成并已进入运行期注册表",
    }
    if normalized_code_name:
        result["code"] = {
            "path": (code_dir / normalized_code_name).relative_to(project_root).as_posix(),
            "name": Path(normalized_code_name).stem,
            "validation": {"valid": True, "message": "代码语法检查通过"},
        }
    return result

__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_service_setting"}]
