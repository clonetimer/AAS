"""Plugin catalog and mutation handlers independent of HTTP dispatch."""
from __future__ import annotations
from pathlib import Path
# 插件不是独立功能页，而是对现有业务链路的增强。这里明确每类能力的
# “贡献点”，供插件管理页解释它实际影响哪里，也避免把仅 CLI 生效的能力
# 误导成 Web 页面能力。
PLUGIN_INTEGRATION = {
    "metrics": {
        "scope": "Web + CLI",
        "hook": "评估指标计算",
        "used_by": "算法运行 / 实验评估",
        "entry": "runner.html",
    },
    "datasources": {
        "scope": "CLI / 配置实验",
        "hook": "数据集读取",
        "used_by": "DatasetManager",
        "entry": "browser.html",
    },
    "preprocessors": {
        "scope": "Web + CLI",
        "hook": "数据预处理管线",
        "used_by": "算法运行 / 清洁数据",
        "entry": "runner.html",
    },
    "reports": {
        "scope": "CLI / 实验输出",
        "hook": "报告渲染",
        "used_by": "实验报告生成",
        "entry": "reports.html",
    },
    "runners": {
        "scope": "CLI / 编排器",
        "hook": "检测任务执行",
        "used_by": "EvaluationOrchestrator",
        "entry": "runner.html",
    },
    "training_strategies": {
        "scope": "Web",
        "hook": "训练流程编排",
        "used_by": "算法运行 / 模型训练",
        "entry": "runner.html",
    },
    "fault_types": {
        "scope": "Web",
        "hook": "故障注入类型",
        "used_by": "模拟故障数据库：动态故障类型与参数",
        "entry": "simulation.html",
    },
    "detectors": {
        "scope": "Web + CLI",
        "hook": "检测算法",
        "used_by": "算法运行",
        "entry": "runner.html",
    },
}


def collect_plugins(root: Path | str | None = None) -> dict:
    """Return catalog for an explicitly selected project root."""
    from core.plugin_runtime import PluginRuntime

    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    out = PluginRuntime(root).collect()
    for ptype, group in out.items():
        group["integration"] = PLUGIN_INTEGRATION.get(ptype, {})
    return out

def install_plugin(root, ptype, yaml_filename, yaml_content,
                  code_filename=None, code_content=None, **kwargs) -> dict:
    """Install every plugin family through the same PluginRuntime."""
    from core.plugin_runtime import PluginRuntime

    result = PluginRuntime(root).install(
        ptype, yaml_filename, yaml_content, code_filename, code_content, **kwargs
    )
    result["plugin_name"] = result.get("plugin")
    if result.get("registered"):
        result["registration_error"] = ""
    else:
        result["registration_error"] = (
            result.get("discovery_error")
            or "; ".join(result.get("contract", {}).get("missing_contract", []))
            or "插件未进入运行期；Python 请检查 class_path/基类，C/C++ 请检查 ABI/动态库导出函数和依赖。"
        )
    return result


def preview_plugin(root, ptype, yaml_filename, yaml_content,
                   code_filename=None, code_content=None, **kwargs) -> dict:
    """Parse and validate plugin files without modifying the project tree."""
    from core.plugin_runtime import PluginRuntime

    return PluginRuntime(root).preview(
        ptype, yaml_filename, yaml_content, code_filename, code_content, **kwargs
    )


def uninstall_plugin(root, ptype, plugin_name, *, force=False) -> dict:
    """Preview or uninstall a user-installed plugin."""
    from core.plugin_runtime import PluginRuntime

    return PluginRuntime(root).uninstall(ptype, plugin_name, force=bool(force))

