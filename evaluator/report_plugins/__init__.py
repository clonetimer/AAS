"""报告格式插件体系（与 detectors 同构）。

- ``base.BaseReportRenderer``：报告渲染器契约（render(ctx, results, out_file)）。
- ``registry``：注册表 + 按格式渲染 + YAML 自动发现。
- ``builtin``：内置 JSON / CSV / Markdown 渲染器。

新增一种报告格式（如 HTML / DOCX / LaTeX）只需写一个渲染器 + 一个 YAML 描述。
"""

from .base import BaseReportRenderer
from .registry import (
    REPORT_REGISTRY,
    available_renderers,
    discover_reports,
    get_renderer,
    register_renderer,
    render_report,
)
from . import builtin  # noqa: F401  (触发内置渲染器注册)

# 自动发现 configs/reports 下的自定义渲染器
discover_reports()
