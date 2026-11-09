"""数据源（格式加载）插件体系（与 detectors 同构）。

- ``base.BaseDataSource``：数据源插件契约（按 format 注册，提供 read_rows / table_exists）。
- ``registry``：注册表 + 按格式解析 + YAML 自动发现。
- ``builtin``：内置 CSV / SQLite 数据源。

新增一种数据源（如 Parquet / MySQL）只需写一个子类 + 一个 YAML 描述，无需改动 DatasetManager。
"""

from .base import BaseDataSource, DataSourceResult
from .registry import (
    DATASOURCE_REGISTRY,
    available_datasources,
    discover_datasources,
    get_datasource,
    register_datasource,
)
from . import builtin  # noqa: F401  (触发内置数据源注册)

# 自动发现 configs/datasources 下的自定义数据源
discover_datasources()
