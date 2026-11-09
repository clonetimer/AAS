# AAS

卫星遥测故障检测与评估系统。当前源码支持时间序列和帧序列两种坐标模式，可从历史库、模拟故障库和用户 CSV 导入数据，进行训练、发布、监督评估与无标签推理。

## 部署

- Windows 离线部署：按 [安装说明](INSTALL_DUAL_COORDINATE_WINDOWS.md) 操作。离线包单独交付，仓库不包含 Python 运行时、依赖 wheel、原始 CSV、数据库或模型产物。
- 已有 V2 库升级：使用 `python -m scripts.migrate_v2_coordinates --help` 查看迁移命令，先在数据库副本验证。
- 从 CSV.GZ 和 targets.zip 建库：使用 `python -m scripts.rebuild_target_labeled_v2 --help` 查看参数。Python 3.8 及更新版本适用。
- 启动服务：`python -m scripts.visualization_server --help`。

## 数据与坐标

时间模式保留真实时间戳；帧模式使用独立的 `frame_sequence_id` 和从 1 开始的 `frame_index`，不生成虚假时间戳。不同来源的同号帧不会自动合并。无标签数据保持未知真值，评估仅输出推理结果。

CSV 字段映射、单位和标签检查由导入向导完成。数据源可以按来源删除；删除前请确认选择的来源和影响范围。

## 验证

核心回归测试位于 `tests/`。源码包另有独立验收报告；仓库不存放历史阶段报告、运行数据库或构建缓存。
