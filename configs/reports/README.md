# 自定义报告格式（插件）

把一种新的报告格式做成插件（如 HTML / DOCX / LaTeX），只需两步，**无需改动 ReportGenerator 与 orchestrator**：

1. 编写渲染器类（继承 `evaluator.report_plugins.base.BaseReportRenderer`）：

```python
from evaluator.report_plugins.base import BaseReportRenderer

class HtmlRenderer(BaseReportRenderer):
    name = "html"                 # 格式键名
    label = "HTML"
    dependencies = ["jinja2"]

    def render(self, experiment_context, results, out_file):
        html = "<h1>评估报告</h1>"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html)
```

2. 在 `configs/reports/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: html
label: HTML
description: HTML 报告
class_path: my_package.my_module.HtmlRenderer
dependencies: [jinja2]
```

放置后该渲染器自动注册，可通过 `render_report("html", ctx, results, out_file)` 调用，
或在 orchestrator 中按 `report_format` 选择。这复用了与检测器（detector）完全相同的插件范式。
