# 数据模板

这里提供两份样例模板：

- [telemetry_template.csv](telemetry_template.csv)：原始遥测数据模板
- [fault_intervals_template.csv](fault_intervals_template.csv)：故障区间说明模板

使用方式：

1. 按 `telemetry_template.csv` 的字段填写你的 CSV 数据。
2. 按 `fault_intervals_template.csv` 的格式填写故障发生区间。
3. 后续我可以把这两份模板合并成历史数据库，再继续做算法评估。

字段约定：

- `timestamp`：时间戳，建议使用 ISO-8601 格式。
- `label`：`0` 表示正常，`1` 表示故障；如果你暂时没有标签，也可以先留空，后续再根据故障区间生成。
