"""执行器插件契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseRunner(ABC):
    """执行器插件契约。

    ``run`` 接收一个 task_spec（与现有 LocalRunner 的 task_spec 结构一致）并返回结果
    dict（包含 ``status`` 与 ``output``）。新增执行后端只需实现此方法并注册。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    dependencies: list[str] = []

    @abstractmethod
    def run(self, task_spec: dict) -> dict:
        raise NotImplementedError
