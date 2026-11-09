"""预处理算子插件契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BasePreprocessOperator(ABC):
    """预处理算子插件契约。

    每个算子对 ``rows``（list[dict]，单序列数据以 ``value`` 列承载）就地变换后返回。
    ``enabled`` 根据 ``pipeline_config`` 决定是否启用（默认始终启用）。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    dependencies: list[str] = []
    order: int = 1000
    default_enabled: bool = True
    config_schema: list[dict[str, Any]] = []

    def enabled(self, config: dict) -> bool:
        """Return whether this operator is active for the current pipeline config."""
        return bool(config.get(self.name, self.default_enabled))

    @abstractmethod
    def apply(self, rows: list[dict[str, Any]], config: dict) -> list[dict[str, Any]]:
        raise NotImplementedError
