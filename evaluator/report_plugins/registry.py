"""报告渲染器注册表、按格式渲染与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class

from .base import BaseReportRenderer


REPORT_REGISTRY: dict[str, BaseReportRenderer] = {}


def register_renderer(renderer: BaseReportRenderer) -> None:
    if not isinstance(renderer, BaseReportRenderer):
        raise TypeError("Report plugin must inherit BaseReportRenderer.")
    if not renderer.name:
        raise ValueError("ReportRenderer must define a non-empty `name`.")
    REPORT_REGISTRY[renderer.name] = renderer


def get_renderer(name: str) -> Optional[BaseReportRenderer]:
    return REPORT_REGISTRY.get(name)


def available_renderers() -> list[dict]:
    return [
        {
            "format": name,
            "label": r.label,
            "description": r.description,
            "dependencies": r.dependencies,
        }
        for name, r in REPORT_REGISTRY.items()
    ]


def render_report(report_format: str, experiment_context: dict, results: list[dict], out_file: str) -> None:
    renderer = get_renderer(report_format)
    if renderer is None:
        raise ValueError(
            f"Unsupported report format: {report_format!r}. "
            f"Available: {sorted(REPORT_REGISTRY)}"
        )
    renderer.render(experiment_context, results, out_file)


def discover_reports(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义报告渲染器。

    YAML 形如::

        name: html
        label: HTML
        description: HTML 报告
        class_path: my_package.my_module.HtmlRenderer
        dependencies: [jinja2]

    返回成功注册的渲染器数量。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent / "configs" / "reports"
    if not search_dir.exists():
        return 0
    count = 0
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict) or "class_path" not in data:
            continue
        try:
            cls = import_plugin_class(data["class_path"], search_dir)
            renderer = cls()
            if data.get("name"):
                renderer.name = data["name"]
            if data.get("label"):
                renderer.label = data["label"]
            if data.get("description"):
                renderer.description = data["description"]
            if data.get("dependencies"):
                renderer.dependencies = data["dependencies"]
            register_renderer(renderer)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
