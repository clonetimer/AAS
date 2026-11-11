# AAS

卫星遥测故障检测与评估系统。当前版本支持时间序列和帧序列两种坐标模式，覆盖历史遥测管理、故障模拟、数据预处理、算法训练、模型发布、监督评估和无标签推理。

## 普通用户从这里开始

- Windows 10/11 离线部署：使用独立交付的 Windows 离线包，按包内「部署说明.md」操作。
- 首次使用：阅读 [AAS 用户快速入门](docs/USER_QUICKSTART.md)。
- CSV 导入：阅读 [CSV 导入向导说明](docs/G0_3_CSV_IMPORT_QUICKSTART.md)。
- Windows 离线部署包的构建和验收：阅读 [离线部署说明](docs/WINDOWS_OFFLINE_DEPLOYMENT.md)。

## 数据存储边界

| 数据用途 | 保存位置 | 是否写入历史遥测库 |
| --- | --- | --- |
| 历史遥测 CSV/CSV.GZ 导入 | 活动历史 V2 数据库 | 是 |
| 故障模拟 CSV | 独立模拟工作区 | 否 |
| 训练 CSV | `datasets/user_csv_workspaces/user_csv_workspace.sqlite` | 否 |
| 测试/评估 CSV | 同一独立用户 CSV 工作区，按用途区分 | 否 |
| 本地 CSV 预处理 | 浏览器选择的输入文件及导出结果 | 否 |

训练和测试 CSV 在独立工作区中持久保存，原始文件字节和 SHA-256 一并保留。任务引用未释放时，系统会阻止删除；用户可在训练/评估页面的「我的 CSV 数据集」中主动删除。不得将训练/测试 CSV 描述为导入历史数据库。

时间模式保留真实时间戳；帧模式使用独立的 `frame_sequence_id` 和从 1 开始的 `frame_index`，不生成虚假时间戳。不同来源的同号帧不会自动合并。无标签数据保持未知真值，评估仅输出推理结果。

## 开发与维护

- 源码安装：按 [Windows 源码部署说明](INSTALL_DUAL_COORDINATE_WINDOWS.md) 操作。该方式需要准备 Python 环境并安装依赖，不等同于完整离线安装包。
- 已有 V2 库升级：使用 `python -m scripts.migrate_v2_coordinates --help` 查看迁移命令，先在数据库副本验证。
- 从 CSV.GZ 和 targets.zip 建库：使用 `python -m scripts.rebuild_target_labeled_v2 --help` 查看参数。Python 3.8 及更新版本适用。
- 启动服务：`python -m scripts.visualization_server --help`。
- 核心回归测试位于 `tests/`；本地测试入口位于 `LOCAL_TEST/`。

仓库不存放用户的运行数据库、真实用户 CSV、模型产物或构建缓存。Windows 离线包由受控构建脚本从源码版本和经过校验的离线依赖介质生成。
