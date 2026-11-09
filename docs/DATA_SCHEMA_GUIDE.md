# 单参量数据存储与元信息目录

## 数据结构

每张**新导入的业务数据表**对应一个测量参量。表中只重复存储逐时间点变化的数据，例如 `timestamp`、`value`、`label`、`id`、`created_at`（如有）。型号、分系统、部件及参量名通过**四级规范化字典**集中管理，不在每条采样行重复保存。`aas_series_catalog` 只保存业务表名、所属参量 ID 和结构类型。

`label` 必须仍按采样记录保存：同一张表可能包含正常与故障时段。表级训练/测试用途继续由 `aas_table_meta` 管理，不能替代采样行的真实故障标签。

所有应用 API 会向数据浏览、训练/评估、故障模拟和导出的数据恢复四个身份字段，兼容旧文件结构。导出的 CSV 保持完整字段，不要求用户自己联表。历史混合参量表只读兼容；训练多表融合时仍拒绝身份不唯一的表。

## 规范化层级目录（独立 SQLite 外键）

新数据库的内部目录使用以下一对多关系，所有表名以 `aas_` 开头，不在数据浏览或 CSV 导出列表中展示：

| 表 | 字段 | 唯一性/关系 |
|---|---|---|
| `aas_models` | `id, name` | 型号名唯一 |
| `aas_subsystems` | `id, model_id, name` | 同型号内分系统名唯一，`model_id` 外键 |
| `aas_devices` | `id, subsystem_id, name` | 同分系统内设备名唯一，`subsystem_id` 外键 |
| `aas_variables` | `id, device_id, name` | 同设备内参量名唯一，`device_id` 外键 |
| `aas_series_catalog` | `table_name, variable_id, schema_kind` | `table_name` 唯一；`variable_id` 外键 |

不同部件可以各自使用 `temperature` 等相同参量名，不会被错误合并。多张正常/测试/故障表可以共用同一参量 ID；**同一个 ID 只在当前 SQLite 文件内有效**，跨数据库同步通过四级名称身份映射，不复制 ID。原始表的 `value` 和逐行 `label` 不受目录规范化影响。

业务端通过 `series_catalog.get()` / `signature()` 获取完整四级身份，不直接读取目录中的历史文本列；管理连接通过 `PRAGMA foreign_keys=ON` 强制验证引用，删除表时按需清理无人使用的四级节点。数据库中存在旧版平面目录时仍可读取、导入，不会在服务启动或普通写入时自动迁移。

## 从旧平面目录迁移到规范化层级目录（请停止服务）

这项迁移**不改写采样行或采样表结构**；只把旧 `aas_series_catalog` 的四个文本列转换为四级字典及外键引用。未迁移的混合表、旧宽表继续保持原状。

```powershell
# 项目根目录，默认 dry-run
python scripts/migrate_series_hierarchy.py --db datasets/history_sample.db
python scripts/migrate_series_hierarchy.py --db datasets/simulation.db

# 停止服务，关闭 SQLite 工具并确认备份位置后执行
python scripts/migrate_series_hierarchy.py --db datasets/history_sample.db --apply
python scripts/migrate_series_hierarchy.py --db datasets/simulation.db --apply
```

执行前通过 SQLite 在线备份生成 `datasets/backups/<数据库名>.before-hierarchy-normalization.bak`；已有同名备份不会覆盖。所有 DDL 和目录数据在事务/保存点内执行；比较**每张业务表完整物理记录、字段顺序、记录数量、原目录每条逻辑身份**，执行 `PRAGMA integrity_check` 和 `foreign_key_check` 后才提交。异常会回滚，备份留存以便人工恢复。已完成迁移时重复执行不会二次改写或生成备份。

升级代码但未迁移本机旧库是支持的：旧 `aas_series_catalog(model, subsystem, device, variable, schema_kind)` 继续可用，之后在维护窗口执行上述脚本即可。**不要用完整工程的示例库覆盖自己的业务数据**；应更新程序文件，迁移自己的数据库。

## 既有采样表安全精简（仅适用于仍含重复身份列的老表）

迁移只处理符合条件的**单参量标准表**：四个身份字段非空且所有行相同，包含 `timestamp/value/label`，不包含未知的额外测量列。清洁表、宽表、混合表及异常结构一律保留，不会猜测或强制修复。

```powershell
# 在项目 AAS 根目录执行。默认仅预览，不写数据库。
python scripts/migrate_series_metadata.py --db datasets/history_sample.db
python scripts/migrate_series_metadata.py --db datasets/simulation.db

# 关闭系统服务及其他 SQLite 占用程序后，主动执行备份和迁移。
python scripts/migrate_series_metadata.py --db datasets/history_sample.db --apply
python scripts/migrate_series_metadata.py --db datasets/simulation.db --apply
```

`--apply` 会先创建 `datasets/backups/<数据库名>.before-series-normalization.bak`，**若备份同名文件已存在则禁止覆盖**。之后在 SQLite 事务中逐表移除冗余列；对迁移前后每条记录的全部字段值进行校验，并通过 `PRAGMA integrity_check` 才提交。若发生异常会回滚事务；原始备份留作人工恢复。可以通过 `--backup-dir <路径>` 设置独立备份目录。

不应把压缩包内提供的示例数据库直接覆盖已有本地业务数据库；有自有数据的用户应仅更新程序代码，再运行上述迁移工具。

## 层级筛选与多表训练

型号、分系统、部件和参量来自目录的统一身份信息。数据浏览器对只有一个可选值的筛选层级展示只读“固定归属”，不必反复点击；多选层级保留原功能。多表训练保持对时间戳进行严格匹配或显式容差匹配，训练模型继续记录各来源表的参量身份，不允许因为表重命名而发生隐式错配。

## 数据兼容边界

- 旧式单参量表即使尚未迁移，仍可浏览、训练、评估及导出。
- 混合多个参量的历史表可以查看，但不能直接视为单参量表进行多表训练。
- 非标准多列测量表和衍生清洁表保留原结构，新表不允许在同一业务表内混装多个传感器量。
- SQLite 可能需要离线 `VACUUM` 才会回收空闲页；本迁移不执行 `VACUUM`，以免改变没有显式主键的旧表的行号语义。
- 新增的元信息目录属于内部系统表，不能作为历史/模拟数据表进行导出或删除。

## 后端回归（不包含界面测试）

主要覆盖：新导入物理结构、身份目录验证、标签分布、时间范围、历史表读取、CSV/ZIP 导出、多表训练、清洁表、故障注入、模拟库同步、目录清理、旧混合表兼容、数据库事务/备份和迁移前后全量内容校验。
