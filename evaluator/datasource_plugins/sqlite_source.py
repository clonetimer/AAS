"""内置 SQLite 数据源。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from scripts.server.sqlite_guard import sqlite_connection as guarded_sqlite_connection

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SIMULATION_DB_PATH = _PROJECT_ROOT / "datasets" / "simulation.db"

from .base import BaseDataSource, DataSourceResult


def _validate_sql_identifier(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


class SqliteDataSource(BaseDataSource):
    name = "sqlite"
    label = "SQLite 数据库"
    description = "从 SQLite 数据库读取表"
    formats = ["sqlite", "db", "historydb"]

    def table_exists(self, config: dict) -> bool:
        file_path = Path(config["file"])
        table_name = config["table"]
        table = _validate_sql_identifier(table_name)
        query = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
        with guarded_sqlite_connection(file_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            row = conn.execute(query, (table,)).fetchone()
        return row is not None

    def read_rows(self, config: dict) -> DataSourceResult:
        file_path = Path(config["file"])
        table_name = config["table"]
        if not file_path.exists():
            raise ValueError(f"Dataset file not found: {file_path}")

        table = _validate_sql_identifier(table_name)
        query = f"SELECT * FROM {table}"
        with guarded_sqlite_connection(file_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
        return DataSourceResult(
            rows=rows,
            meta={
                "source_file": str(file_path.resolve()),
                "data_source_table": table_name,
            },
        )
