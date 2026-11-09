"""报告渲染器插件契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseReportRenderer(ABC):
    """报告渲染器插件契约。

    ``render`` 将评估结果写入 ``out_file``。``experiment_context`` 提供实验元信息
    （experiment_id / timestamp 等），``results`` 为各检测器结果列表。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    dependencies: list[str] = []

    @abstractmethod
    def render(self, experiment_context: dict, results: list[dict], out_file: str) -> None:
        raise NotImplementedError
