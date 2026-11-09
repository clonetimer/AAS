"""Experiment report export service backed by report renderer plugins."""

from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any

from evaluator.report_plugins.registry import get_renderer
from core.path_policy import resolve_managed_file

_SAFE_EXT = re.compile(r"^[A-Za-z0-9]{1,12}$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _resolve_summary(root: Path, relative_path: str) -> Path:
    return resolve_managed_file(
        root / "outputs",
        relative_path,
        anchor=root,
        required_name="summary.json",
        must_exist=True,
    )


def _renderer_extension(renderer: Any, format_name: str) -> str:
    extension = str(getattr(renderer, "file_extension", "") or format_name).lstrip(".")
    if not _SAFE_EXT.fullmatch(extension):
        raise ValueError("报告插件声明了非法文件扩展名")
    return extension.lower()


def render_experiment_report(root: str | Path, relative_summary_path: str, format_name: str) -> dict[str, Any]:
    """Render one experiment through a registered report contribution.

    The plugin receives the experiment metadata and the original result records.
    The output path is host-controlled and kept next to the source summary.
    """
    root = Path(root)
    summary_path = _resolve_summary(root, relative_summary_path)
    renderer = get_renderer(format_name)
    if renderer is None:
        raise ValueError(f"未知报告格式: {format_name}")

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("实验摘要格式无效")

    experiment = dict(payload.get("experiment") or {})
    # Preserve type-specific metadata without forcing plugins to reopen files.
    for section in ("training", "evaluation"):
        value = payload.get(section)
        if isinstance(value, dict):
            experiment[section] = value
    results = [item for item in (payload.get("results") or []) if isinstance(item, dict)]

    extension = _renderer_extension(renderer, format_name)
    experiment_id = str(experiment.get("experiment_id") or summary_path.parent.parent.name)
    safe_experiment_id = _SAFE_NAME.sub("_", experiment_id).strip("._") or "experiment"
    safe_format = _SAFE_NAME.sub("_", format_name).strip("._") or "report"
    filename = f"experiment_{safe_experiment_id}_{safe_format}.{extension}"
    output_path = summary_path.parent / filename

    renderer.render(experiment, results, str(output_path))
    if not output_path.is_file():
        raise RuntimeError("报告插件未生成输出文件")

    return {
        "format": format_name,
        "filename": filename,
        "media_type": str(getattr(renderer, "media_type", "application/octet-stream") or "application/octet-stream"),
        "path": output_path,
        "size": output_path.stat().st_size,
    }
