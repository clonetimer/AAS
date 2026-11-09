"""内置报告渲染器：JSON / CSV / Markdown。"""

from __future__ import annotations

import csv
import json

from .base import BaseReportRenderer
from .registry import register_renderer


class JsonReportRenderer(BaseReportRenderer):
    name = "json"
    label = "JSON"
    description = "结构化 JSON 摘要"

    def render(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        experiment_id = experiment_context.get("experiment_id")
        experiment_type = experiment_context.get("type", "evaluation")
        experiment = {
            "experiment_id": experiment_id,
            "type": experiment_type,
            "run_dir": experiment_context.get("run_dir"),
            "dataset": experiment_context.get("dataset"),
            "table": experiment_context.get("table"),
        }
        report = {
            "experiment_id": experiment_id,
            "timestamp": experiment_context.get("timestamp"),
            "experiment": experiment,
            "detectors": [item["detector"] for item in results],
            "results": results,
        }
        if experiment_type == "evaluation":
            report["evaluation"] = {
                "eval_dataset": experiment_context.get("dataset"),
                "eval_table": experiment_context.get("table"),
            }
        elif experiment_type == "training":
            report["training"] = {
                "algorithm": ", ".join(item["detector"] for item in results),
                "dataset": experiment_context.get("dataset"),
                "table": experiment_context.get("table"),
            }
        with open(out_file, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)


def _extract_tabular(item: dict) -> tuple[list[str], dict]:
    """从单条 result item 抽取干净表格行，兼容两种形态：

    - 真实编排结果：指标位于 ``item["metrics"]`` 子字典，原始结果位于
      ``item["raw_result"]``（含 anomaly_score / pred_label 等长数组，体积大，
      仅保留在 summary.json，不应进入表格）。
    - 扁平 / 测试形态：指标字段直接位于 item 顶层（排除 detector / raw_result）。

    统一返回 ``(metric_keys, row)``，row 含 ``detector``、``status`` 与各项指标标量，
    保证 CSV / Markdown 输出为干净的“表格化指标”。
    """
    raw_result = item.get("raw_result")
    status = raw_result.get("status", "unknown") if isinstance(raw_result, dict) else "unknown"

    metrics = item.get("metrics")
    if hasattr(metrics, "to_dict"):  # MetricsResult 等封装对象
        metrics = metrics.to_dict()
    if not isinstance(metrics, dict):
        metrics = None

    if isinstance(metrics, dict):
        metric_keys = list(metrics.keys())
        row = {"detector": item.get("detector", "unknown"), "status": status}
        row.update({key: metrics.get(key, "") for key in metric_keys})
    else:
        # 扁平形态：取顶层字段（排除 detector 与原始结果大字典）
        metric_keys = [key for key in item.keys() if key not in ("detector", "raw_result")]
        row = {"detector": item.get("detector", "unknown"), "status": status}
        row.update({key: item.get(key, "") for key in metric_keys})
    return metric_keys, row


class CsvReportRenderer(BaseReportRenderer):
    name = "csv"
    label = "CSV"
    description = "CSV 汇总表（指标表格化，原始结果存于 summary.json）"

    def render(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        if not results:
            with open(out_file, "w", encoding="utf-8") as handle:
                handle.write("detector,status\n")
            return
        # 仅输出 detector + status + 指标列；
        # 原始结果（raw_result 含 anomaly_score / pred_label 等长数组）移出主表，
        # 仅完整保留在 summary.json，避免 CSV 单元格被大字典撑爆。
        fieldnames = ["detector", "status"]
        metric_keys_union: list[str] = []
        rows: list[dict] = []
        for item in results:
            keys, row = _extract_tabular(item)
            rows.append(row)
            for key in keys:
                if key not in metric_keys_union:
                    metric_keys_union.append(key)
        fieldnames.extend(metric_keys_union)
        with open(out_file, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})


class MarkdownReportRenderer(BaseReportRenderer):
    name = "markdown"
    label = "Markdown"
    description = "Markdown 评估报告"

    def render(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        lines = [
            "# 异常检测评估报告",
            "",
            f"- 实验 ID: {experiment_context.get('experiment_id', 'N/A')}",
            f"- 时间戳: {experiment_context.get('timestamp', 'N/A')}",
            f"- 实验类型: {experiment_context.get('type', 'evaluation')}",
            f"- 数据集: {experiment_context.get('dataset', 'N/A')}",
            f"- 数据表: {experiment_context.get('table', 'N/A')}",
            f"- 检测器数量: {len(results)}",
            "",
            "## 各检测器结果",
            "",
        ]
        for item in results:
            detector = item.get("detector", "未知")
            lines.append(f"### 检测器: {detector}")
            _, row = _extract_tabular(item)
            # 原始结果（raw_result 大字典）不写入报告，仅存于 summary.json
            for key, value in row.items():
                if key == "detector":
                    continue
                lines.append(f"- {key}: {value}")
            lines.append("")
        with open(out_file, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


def _register_builtins() -> None:
    for renderer in (JsonReportRenderer(), CsvReportRenderer(), MarkdownReportRenderer()):
        register_renderer(renderer)


_register_builtins()
