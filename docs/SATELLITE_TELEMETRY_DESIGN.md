# AAS 遥测数据管理设计与本轮改进

## 采用成熟系统思路，而非照搬地面站软件

- Yamcs Mission Database：任务定义按层级组织、参量使用完整路径，数据归档与参量定义分离。
  https://docs.yamcs.org/yamcs-server-manual/mdb/
- Yamcs Parameter Archive：每个参量保存自己的生成时刻、工程值、原始值和状态；同时出现的参量可共享时间戳组以改善存储效率。
  https://docs.yamcs.org/yamcs-server-manual/data-management/parameter-archive/
- Yamcs 参数列表和 CSV 导出：参量组合是用户资源，支持按时间窗读取和导出。
  https://docs.yamcs.org/yamcs-server-manual/web-interface/telemetry/
- Open MCT Time Conductor / Stacked Plot：统一时间范围，叠加或堆叠多参量时不强制物理上共享采样时间戳。
  https://nasa.github.io/openmct/static/files/Open_MCT_Users_Guide.pdf
- XTCE / CCSDS 660.1：为遥测定义参数类型、单位、校准、有效范围和告警语义。
  https://ccsds.org/Pubs/660x1g2.pdf

## 本轮已完成

1. `sqlite_tables` 目录除身份、列和用途外，增加真实 `time_start`、`time_end`、`row_count`。非时间表返回 null，不虚构时间范围。此实现不改变采样记录和现有外键目录。
2. 三个页面复用的资源选择器同时显示层级归属、数据角色、样本数和时间覆盖；角色也进入搜索关键词。独立测试集选择器同样使用完整目录元数据。
3. 删除“跨域评估”额外开关。由用户选中的测试表定义评估输入，服务端对训练/评估每个特征位置计算域差异，并在 `input_contract.domain_differences` 中返回结果；前端自动保存到实验历史配置，评估日志显示识别出的参量关系。可区分 `same_domain`、`cross_domain` 和 `unverified_domain`。
4. 自动匹配身份一致的测试表仅作为**推荐**，不再隐藏其他合法测试表；手动选择的特征顺序不得被身份匹配逻辑重新排序。
5. 保留必须遵守的技术契约：输入表数量、模型特征维度、表用途合法性、清洁/原始视图、训练期归一化参数与同步策略。类别差异不构成拒绝理由。
6. 若模型无可验证的身份目录，标记身份不明，不能擅自认定为同域。此时不自动选中表，需用户明确选择。

## 为什么这轮**不**把原始表合并成永久大宽表？

多数遥测源独立采样、存在不同时间戳、频率、缺测和数据有效期。把所有值永久合并到一张共享时间戳表，会人为引入大量空值；再隐式填充可能泄漏未来信息。当前选择继续将真实采样流独立存储，运行训练/评估时根据显式策略生成临时联合样本。

## 未来数据库增强方案（**本轮未迁移，不应误认为已完成**）

1. **参量字典**：在现有 `aas_variables` 上增加版本化 `units`、`engineering_type`、`calibrator_ref`、`valid_range`、`nominal_sampling_period`、`validity_seconds`。字段变化要能按时间或字典版本追溯，不能强制覆盖历史遥测定义。
2. **采样时间及质量**：可选区分遥测产生时刻 `generation_time` 和系统接收时刻 `acquisition_time`，以及原始值、工程值、数据质量标识。尚无时间域约定的数据集不能默认全部当作 UTC。
3. **数据集和谱系**：引入独立 `dataset/run` 层、`series_id`、来源文件、导入版本、数据角色、分割协议；把训练/评估的数据集组合保存为不可变实验配置，而不是依赖临时表名顺序。
4. **集合与工作区**：支持保存“常用参量列表/对比视图”、时间窗、曲线显隐设置；多人使用时再增加权限。
5. **性能路线**：有需求时先给 `(timestamp)` 建索引，采用按范围分页、聚合下采样和缓存；只有规模足够大才评估 TimescaleDB、列式存储或专门遥测归档引擎。

这些能力宜在独立迁移阶段逐项落地，先构建新旧模型共存、数据不变性及回滚测试，不宜一次性替换现有可用结构。

## 评估的业务边界

允许跨型号、跨设备和跨参量，不额外勾选“跨域”。但必须保留模型参数、归一化方法和维度契约，计算出的分数可能缺乏直接物理可比性。模型指标只对目标数据标签有效时才有意义，跨域不等于跨域方法已验证。

训练表/测试表的角色筛选目前依旧有效。若未来允许使用训练表做评估，应在输出中明确标识“训练内回测”，避免把非独立样本的指标描述为泛化性能。

## 测试范围

`pytest -q` 全套回归；`node --check` 检查 JavaScript；`node tests/runner_selection_behavior.cjs` 验证训练/评估选表逻辑；`pylint --disable=C,R` 检查修改的 Python 模块。完整 Windows 浏览器点击、遥测时钟同步基准标定以及百万级性能压力测试未在本轮完成。
