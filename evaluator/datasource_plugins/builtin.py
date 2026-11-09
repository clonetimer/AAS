"""内置数据源注册。"""

from __future__ import annotations

from .csv_source import CsvDataSource
from .registry import register_datasource
from .sqlite_source import SqliteDataSource


def _register_builtins() -> None:
    for ds in (CsvDataSource(), SqliteDataSource()):
        register_datasource(ds)


_register_builtins()
