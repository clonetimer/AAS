"""数据源注册表、按格式解析与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class

from .base import BaseDataSource


DATASOURCE_REGISTRY: dict[str, BaseDataSource] = {}


def register_datasource(datasource: BaseDataSource) -> None:
    if not isinstance(datasource, BaseDataSource):
        raise TypeError("DataSource plugin must inherit BaseDataSource.")
    if not datasource.formats:
        raise ValueError("DataSource must declare `formats`.")
    for fmt in datasource.formats:
        DATASOURCE_REGISTRY[fmt] = datasource


def get_datasource(data_format: str) -> Optional[BaseDataSource]:
    return DATASOURCE_REGISTRY.get(data_format)


def available_datasources() -> list[dict]:
    """Return one catalog item per datasource plugin, not per format alias.

    A datasource may intentionally register several compatible format keys
    (SQLite uses ``sqlite``, ``db`` and ``historydb``).  The runtime registry
    must keep those aliases for backwards compatibility, but the plugin catalog
    should display the datasource capability only once.
    """
    items: list[dict] = []
    seen: set[int] = set()
    for fmt, ds in DATASOURCE_REGISTRY.items():
        identity = id(ds)
        if identity in seen:
            continue
        seen.add(identity)
        formats = [str(value) for value in (getattr(ds, "formats", None) or [fmt])]
        items.append({
            "format": formats[0] if formats else str(fmt),
            "formats": formats,
            "name": ds.name or str(fmt),
            "label": ds.label,
            "description": ds.description,
            "dependencies": getattr(ds, "dependencies", []),
            "available": ds.available(),
        })
    return items


def discover_datasources(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义数据源。

    YAML 形如::

        name: parquet
        label: Parquet 文件
        description: 从 Parquet 读取数据集
        formats: [parquet]
        class_path: my_package.my_module.ParquetDataSource
        dependencies: [pyarrow]

    返回成功注册的数据源数量（一个数据源可声明多个 formats）。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent / "configs" / "datasources"
    if not search_dir.exists():
        return 0
    count = 0
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict) or "class_path" not in data:
            continue
        try:
            cls = import_plugin_class(data["class_path"], search_dir)
            ds = cls()
            if data.get("name"):
                ds.name = data["name"]
            if data.get("label"):
                ds.label = data["label"]
            if data.get("description"):
                ds.description = data["description"]
            if data.get("formats"):
                ds.formats = list(data["formats"])
            if data.get("dependencies"):
                ds.dependencies = data["dependencies"]
            register_datasource(ds)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
