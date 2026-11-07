# P3-D 实施路线

采用方案 A，按依赖顺序实施，全部工作在功能分支进行。

1. **统一任务生命周期与取消 API**：扩展 TaskRuntime，支持 create_task、request_cancel_task、is_cancel_requested、set_task_cancellable；新增通用 task-status、task-history 全量查询和确认取消接口；保留旧训练状态 API 兼容。
2. **算法子进程取消**：AlgorithmProcessService 增加取消回调，复用 POSIX 进程组和 Windows taskkill /T 终止进程树；训练在模型校验、来源凭据和报告归档阶段关闭取消。
3. **评估/仅推理异步化**：监督评估和无标签推理统一返回 task_id；阶段进度、失败诊断、报告归档与取消状态写入 TaskRuntime。
4. **批量导入安全取消**：Phase 49 的逐文件收据日志保留，任务主状态映射到 TaskRuntime；仅在文件事务边界检查取消，保留已提交文件和幂等重试。
5. **CSV/CSV.GZ 磁盘暂存和流式导入**：请求体分块写入临时文件，压缩流受限解压，逐行校验，原子写库；取消/超限/损坏均清理临时文件且不留半成品。
6. **前端统一任务体验**：训练、评估、仅推理和批量导入显示进度与终态；支持取消确认；历史页支持查看全部任务和重试入口。

## 验证门槛
- 每个任务先写失败测试，确认 RED 后实现并验证 GREEN。
- 覆盖取消/完成竞争、服务重启、不可取消阶段、文件事务边界、gzip 解压上限和临时文件清理。
- 执行相关 Python、Node、JS 语法与 Windows 专项回归；区分基线失败与本轮回归。
- 每个阶段提交源码、测试与说明；生成候选 ZIP 和 SHA-256。
