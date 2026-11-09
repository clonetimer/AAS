"""数据源插件契约与统一结果。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataSourceResult:
    """数据源读取结果。"""

    rows: list[dict[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class BaseDataSource(ABC):
    """数据源插件契约。

    通过 ``formats`` 声明可处理的格式键（如 csv / sqlite），运行时由注册表按
    ``dataset_config["format"]`` 解析。``read_rows`` 返回原始行（list[dict]），
    有「表」概念的数据源（如数据库）需覆写 ``table_exists``。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    formats: list[str] = []
    dependencies: list[str] = []

    def available(self) -> bool:
        for dep in self.dependencies:
            try:
                __import__(dep)
            except ImportError:
                return False
        return True

    @abstractmethod
    def read_rows(self, config: dict) -> DataSourceResult:
        raise NotImplementedError

    def table_exists(self, config: dict) -> bool:
        # 默认无「表」概念（如 CSV）；有表的数据源需覆写
        del config
        return True
