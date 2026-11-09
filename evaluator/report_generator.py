"""评估报告生成器（外观层）。

内置 JSON / CSV / Markdown 三种格式已改为插件，统一由 ``evaluator.report_plugins``
注册表驱动；本类仅作为向后兼容的外观，将调用委托给对应渲染器插件。
"""

from __future__ import annotations

from evaluator.report_plugins.registry import get_renderer


class ReportGenerator:
    """评估报告生成器。"""

    def generate_summary_json(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        get_renderer("json").render(experiment_context, results, out_file)

    def generate_csv(self, results: list[dict], out_file: str) -> None:
        get_renderer("csv").render({}, results, out_file)

    def generate_markdown_report(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        get_renderer("markdown").render(experiment_context, results, out_file)
