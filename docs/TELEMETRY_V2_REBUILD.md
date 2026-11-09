# AAS 遥测数据库 V2：重建方案、迁移边界与使用手册

日期：2026-10-20；适用于原始工程 `20261020_4.zip` + Phase 3 跨表逻辑参量补丁。

## 1. 为什么要重建

旧数据库将“物理数据表”同时充当业务参量、导入批次、训练/测试数据集和预处理产物。由此出现近百张同名模式表、宽表混用、缺失目录登记、同参量跨表浏览困难。与其继续对表名进行特殊判断，不如把 **卫星与参量身份、数据集用途、时间分片、采样数据** 分开建模。

V2 的物理样本存储采用 **一张规范化窄事实表 + 参量/分片索引**，而不是一张包含所有传感器列的宽表。各参量可以具有不同时间戳、采样率和单位。对于超大规模任务，后续可按卫星及时间切分文件或引入列式归档，而不改变上层逻辑数据模型。

## 2. 模型（新文件内均为 `v2_` 前缀）

| 表 | 职责 |
|---|---|
| `v2_sources` | 每个旧 SQLite 源数据库的命名空间、快照 SHA-256、来源文件名及行数 |
| `v2_channels` | 唯一参量身份：来源命名空间、卫星实例（当前历史库未知，空）、型号、分系统、部件、参量、可选单位 |
| `v2_segments` | 逻辑时间分片/数据集：原表名、规范参量、用途及用途证据、时间类型、表结构和行数 |
| `v2_samples` | 所有已映射的标准 `timestamp/value/label` 采样；保留原始行 JSON，不猜测缺失单位与时区 |
| `v2_legacy_rows` | 未能安全识别的宽表与全部旧管理目录的原始行，等待人工映射，不删除 |
| `v2_issues` | 各来源的迁移疑点：身份不确定、宽表、时间基准、数据用途不明等 |
| `v2_meta` | 新库版本与安全策略 |

当前身份键为：`namespace + satellite_instance + model + subsystem + device + variable`。不同数据库中同名通道 **不自动合并**；需由用户提供明确的卫星实例、观测时基和数据集来源映射后才能安全联查。

**时间语义：**
- 带 `Z`/明确时区偏移的 ISO 时间归类 `datetime_utc`，单独存储 UTC 微秒索引，保留原字符串；
- 无时区日期字符串归类 `datetime_naive`，只进行同域排序，不擅自视为 UTC；
- 数字、科学计数法的时间归类 `numeric_unspecified`，不认定其单位为秒或其起点为 UNIX 历元；
- 无效、空值或不同时间域混合时，保留记录，但禁用该分片的正式时间序列读取；
- 统一时基前严禁直接拼接不同时间域；不自动进行插值。

**角色/泄漏防护：**
- `aas_table_meta` 明确声明的 `train`/`test` 角色可信，原始文件中未声明角色保持 `unspecified`；
- 通过名称后缀 `_train`、`_test`、`_clean` 推测的角色，记录为 `name_hint_only`，默认**不能被只读新查询适配器作为训练/测试输入**；
- 同一数据的原始、训练、测试、清洁、仿真、故障注入派生分片全部保留不同 `segment`，绝不作为独立样本盲目相加；
- 相同时间戳出现重复/矛盾值时不覆盖、不自动合并。当前只读查询返回来源表名与原始行序号，由调用端明确处理；旧平台原有跨表去重与冲突拒绝流程仍不变。

## 3. 实际样例库重建结果

四个上传的示例文件共 **209 张用户定义表（含 21 张管理表）/ 161,131 行**：

| 来源文件 | 表（含管理表） | 原始行 | 规范采样 | 历史保留行 |
|---|---:|---:|---:|---:|
| `history_sample.db` | 147 | 99,487 | 98,530 | 957 |
| `simulation.db` | 53 | 52,011 | 51,890 | 121 |
| `historydb.db` | 3 | 660 | 0 | 660 |
| `history_manifest.db` | 6 | 8,973 | 0 | 8,973 |
| **合计** | **209** | **161,131** | **150,420** | **10,711** |

非标准宽表有 9 张，其数据共 10,280 行；另有旧内部目录 21 张，共 431 行，均在保留区。另有 179 张规范采样表，其中只有部分明确赋予训练或测试角色；不应把“规范行”与“经批准可训练行”等同。

> 注意：此处“保留完整”指所有输入 SQLite **用户定义表**的实际行均以结构化采样行（含原始 JSON）或不可映射原始行记录入 V2。SQLite 内部表、触发器和索引不会原样重建；对应来源表 SQL 结构保存在 `v2_segments.schema_sql`，原始文件保持不变。

## 4. 操作方法

先停止 AAS 服务并备份业务数据库，避免扫描期间输入仍被改写。所有命令在 AAS 项目根目录执行：

**仅预览（默认不创建/修改数据库）：**

```powershell
python scripts/rebuild_telemetry_v2.py --db datasets/history_sample.db --db datasets/simulation.db --report datasets/v2_scan.json
```

**实际创建全新的 V2 文件：**

```powershell
python scripts/rebuild_telemetry_v2.py --db datasets/history_sample.db --db datasets/simulation.db --db datasets/historydb.db --db datasets/history_manifest.db --apply --out datasets/aas_telemetry_v2.sqlite --report datasets/v2_migration_report.json
```

**查询与导出（只读；显式指定数据用途）：**

```powershell
python scripts/query_telemetry_v2.py status --db datasets/aas_telemetry_v2.sqlite
python scripts/query_telemetry_v2.py channels --db datasets/aas_telemetry_v2.sqlite --namespace history_sample
python scripts/query_telemetry_v2.py query --db datasets/aas_telemetry_v2.sqlite --namespace history_sample --model GF1 --subsystem subsys1 --device battery --variable capacity --role train --limit 20
python scripts/query_telemetry_v2.py query --db datasets/aas_telemetry_v2.sqlite --namespace history_sample --model GF1 --subsystem subsys1 --device battery --variable capacity --role test --csv datasets/capacity_test.csv
```

`--out` 不得等于任何输入源且 **不会覆盖已存在文件**；先删掉错误的新输出文件后才能重新构建，不影响输入。`--report` 也拒绝覆盖已有报告。迁移使用 SQLite 在线快照读取旧库，输出先写 `.partial`，完成行数、外键和 `integrity_check` 校验后才发布正式文件。

**回滚**：原数据库未修改；停止使用 V2 文件并切回原平台数据库配置即可。不要用新文件覆盖历史旧库的同名文件。

## 5. 下一步对接工作（尚未实现）

1. 将 Web 数据浏览、导入、删除、预处理及仿真统一改用 V2 目录接口，而不是直接 `CREATE TABLE/ DROP TABLE`。这是**应用层切换**，不能只将 `history_sample.db` 文件路径指向 V2。
2. 原始遥测写入增加正式的卫星实例 ID、参量单位、数据来源、时间基准及入库批次身份。对 `datetime_naive` 等未明确时区数据必须由用户确认时基，禁止程序擅自修正。
3. 将已确认的正常/故障事件区间单独归档，保留事件来源、标签版本及跨参量映射；不要从训练标签反向推断事件。
4. 对 9 张历史宽表提供明确的列→参量映射清单后再迁移，避免把 `f1`、`f2`、`f3` 误认为已知仪器通道。
5. 将实验引用调整为稳定的 `channel_id/segment_id + 时间窗口 + 数据指纹`，记录重复时间戳冲突与数据派生关系；模型接口与算法后处理职责保持不变。
6. 在真实业务库上跑 dry-run、迁移及端到端浏览/训练/评估回归后，再切换主数据库。现阶段为**数据层 V2 候选版**，不宣称已在 Web UI 中启用。

## 6. 为什么暂不覆盖老数据库

旧 Web 服务将一张物理表视为一个可选训练/评估资源，与新 V2 中“一个参量有多段数据”的模型不一致。直接替换旧文件会令训练、导出、删除、备份等模块产生兼容性风险。保留原库、先落地 V2 格式和迁移工具，再逐模块迁移读写适配器，是可验证、可回滚且不丢失已有实验依据的路径。
