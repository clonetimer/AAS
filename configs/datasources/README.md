# 自定义数据源（插件）

把一种新的数据来源做成插件（如 Parquet / MySQL / Kafka），只需两步，**无需改动 DatasetManager 的编排逻辑**：

1. 编写数据源类（继承 `evaluator.datasource_plugins.base.BaseDataSource`）：

```python
import pyarrow.parquet as pq
from evaluator.datasource_plugins.base import BaseDataSource, DataSourceResult

class ParquetDataSource(BaseDataSource):
    name = "parquet"
    label = "Parquet 文件"
    formats = ["parquet"]          # 在 dataset_config["format"] 中使用的键
    dependencies = ["pyarrow"]     # 缺失时自动置灰

    def read_rows(self, config: dict) -> DataSourceResult:
        table = pq.read_table(config["file"])
        rows = [dict(zip(table.column_names, vals)) for vals in zip(*table.to_pydict().values())]
        return DataSourceResult(rows=rows, meta={"source_file": config["file"]})
```

2. 在 `configs/datasources/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: parquet
label: Parquet 文件
description: 从 Parquet 文件读取数据集
formats: [parquet]
class_path: my_package.my_module.ParquetDataSource
dependencies: [pyarrow]
```

放置后该数据源会自动注册，在 `dataset_config` 里把 `format` 设为 `parquet` 即可使用。这复用了与检测器（detector）、
评估指标（metric）完全相同的插件范式：契约基类 + 注册表（按 format 索引）+ YAML 自动发现 + 依赖检测。
