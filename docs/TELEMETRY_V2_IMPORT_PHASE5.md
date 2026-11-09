# AAS V2 Phase 5A：原生 CSV 导入与分片来源管理

状态：**候选版本，尚未进行用户现场端到端验收**。在 Phase 4 浏览层上新增 V2 原生导入，不再从旧数据库转存新数据。

## 从界面操作

「数据浏览」→ 展开「导入 V2 遥测数据」→ 选择单参量 CSV → 填写数据集命名空间、卫星实例（可空）、型号、分系统、部件、参量名称、物理单位、用途及时间域 → **校验并预览** → 核对样本数、时间覆盖、SHA-256 和用途 → **确认导入 V2**。

CSV 使用 UTF-8、表头至少包含 `timestamp,value,label`，`label` 必须为 `0`（正常）或 `1`（故障）。支持其他附加列，它们只随原始行 JSON 保存，**不会自动成为第二个参量或训练特征**。需要导入多个参量时，分别选择相应单参量 CSV。

示例（UTC，必须显式 Z/时区）：

```csv
timestamp,value,label
2026-10-01T00:00:00Z,100.5,0
2026-10-01T00:00:01Z,101.2,0
```

数值时戳需要额外明确**同一个连续时间基准名称**和单位（s/ms/us/ns），例如 `simulation_run_084_elapsed`、`s`，不能把不同仿真的“第 1 秒”合并。新的无时区日期字符串不予导入；必须先确定实际时区并由用户按真实语义规范转换为有时区时间戳。

建议不同卫星任务、不同设备实例、时基不一致或单位不一致时使用不同的完整身份，不要把不同来源简单命名成相同参量。旧库中尚未核定的单位或用途，不允许由新上传直接认定。

## 数据接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v2/telemetry/import-csv` | JSON 请求；`commit=false` 校验预览，`commit=true` 事务导入 |
| GET | `/api/v2/telemetry/imports?limit=30` | 仅查看用户明确导入的 V2 分片记录 |
| GET | `/api/v2/telemetry/series/search?role=raw` | 返回已确认原始数据的逻辑参量 |
| POST | `/api/v2/telemetry/query` | 按 `channel_ids` 查询、跨分片绘制曲线 |

`import-csv` JSON 必填字段：`namespace`, `model`, `subsystem`, `device`, `variable`, `unit`, `role`, `time_domain`, `filename`, `csv`；可选 `satellite_instance`。数字时间还必须提供 `time_reference` 和 `time_unit`。数据库路径固定为服务端 `datasets/aas_telemetry_v2.sqlite`，**请求方不能指定文件系统路径**。新增 `raw` 作为显式原始数据用途；遗留 `unspecified` 仍表示用途未核实，二者不能自动混用。

分片记录以 `role_provenance=user_explicit` 标识；上传来源在 `v2_sources` 中持久保存文件名、SHA-256、字节数和导入 UTC 时间。原始行保留在 `v2_samples.raw_row_json`，`source_ordinal` 对应 **CSV 原始数据行序**（从 0 开始）。用户数据集命名空间 `v2_channels.namespace` 可跨批次复用，系统来源命名空间 `v2_sources.namespace` 每次上传唯一；两者不是同一概念。

附加表 `v2_segment_clock` 在首次确认导入时事务创建，逐个记载数值时钟身份和单位；不修改旧 V2 表或 `schema_version=2`。若将来升级数据库版本，请纳入正式迁移管理。

## 安全与容量

- 单次 CSV 不超过 **4MB / 50,000 条采样**，HTTP 请求体不超过 **8MB**。
- 同一 CSV 内重复时间戳一律拒绝；相同 `channel_id + role + time_domain` 中发现已存在的时间戳则拒绝导入，不自动覆盖或去重。
- 相同参量的物理单位和时间域必须一致；数字时间还必须有一致的基准及单位，未知数字时基与已声明时基禁止混合。不同用途的分片分别查询；训练数据中出现故障标签则拒绝导入。
- 写入使用 `BEGIN IMMEDIATE`，冲突检查和插入同一事务；任何失败则回滚。预检也不能改变数据库。
- **没有物理删除、角色重写或自动模型训练**；旧版本导入/删除仍只影响旧库。不要以 `raw`、`unspecified`、`test` 拼成同一训练数据集。
- 目前的时间序列浏览读取每参量最多 5000 条物理记录（UI 默认 3000），不等于可一次加载的完整原始数据集；跨时窗需要分次查询。

## 安装与验证

1. 备份 `AAS/datasets` 和原工程，停止服务。
2. 在 Phase 4 或原始工程上解压 Phase 5 累积更新包至 `AAS` 父目录。**包内没有数据库文件**。
3. 如尚未安装 V2 样例库，参照 Phase 4 文档单独安装 `datasets/aas_telemetry_v2.sqlite`，绝不覆盖自己的正式库。
4. 执行 `python -m pytest -q tests/test_telemetry_v2_native_import.py tests/test_telemetry_v2_browser_phase4.py tests/test_telemetry_v2_rebuild.py`。
5. 在正式系统导入前先于数据库副本上测试预检、写入与跨分片查询，并验证备份恢复。

## 尚未实施的 V2 收敛项

- 训练/评估页面基于 `channel_id` 的输入契约、特征对齐与模型发布后复现。
- V2 数据集派生链、预处理写回、事件区间引用与对象级标签。
- V2 批次撤销、删除审批、审计日志、可恢复备份及长期性能扩容。

旧版训练/评估仍依赖 Phase 3 的入口，**不要把 V2 浏览成功当作新数据已经可以参加训练/评估**。
