"""内置 CSV 数据源。"""

from __future__ import annotations

import csv
from pathlib import Path

from .base import BaseDataSource, DataSourceResult


class CsvDataSource(BaseDataSource):
    name = "csv"
    label = "CSV 文件"
    description = "从 CSV 文件读取数据集"
    formats = ["csv"]

    def read_rows(self, config: dict) -> DataSourceResult:
        file_path = Path(config["file"])
        if not file_path.exists():
            raise ValueError(f"Dataset file not found: {file_path}")
        with file_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return DataSourceResult(
            rows=rows, meta={"source_file": str(file_path.resolve())}
        )
