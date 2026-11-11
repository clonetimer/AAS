# AAS 文档索引

## 普通用户

- [用户快速入门](USER_QUICKSTART.md)：安装后首次登录、数据导入、故障模拟、训练、评估和结果查看。
- [CSV 导入向导](G0_3_CSV_IMPORT_QUICKSTART.md)：历史库、模拟工作区、训练/测试独立 CSV 工作区的区别和四步导入流程。
- [Windows 离线部署](WINDOWS_OFFLINE_DEPLOYMENT.md)：离线包结构、安装、启动、停止、卸载和验收边界。
- [多表遥测用户指南](MULTI_TABLE_USER_GUIDE.md)：历史遥测参量筛选与浏览。
- [数据导入指南](DATA_IMPORT_GUIDE.md)：字段、标签、单位和来源规则。

## 开发与维护

- [数据结构说明](DATA_SCHEMA_GUIDE.md)
- [实验输入证据](EXPERIMENT_INPUT_EVIDENCE.md)
- [实验来源追溯](EXPERIMENT_PROVENANCE.md)
- [事件级评估协议](event_evaluation_protocol.md)
- [Windows 源码部署说明](../INSTALL_DUAL_COORDINATE_WINDOWS.md)
- [V2 重建方案](TELEMETRY_V2_REBUILD.md)
- [升级交付说明](UPGRADE_DELIVERY_PACKAGE.md)

## 历史设计记录

`TELEMETRY_V2_PHASE*.md`、`superpowers/plans/` 和 `superpowers/specs/` 保存阶段性设计、实现计划和验收证据。它们用于开发追溯，不应替代普通用户快速入门。

当前数据工作流包含历史 V2 数据库、独立故障模拟工作区、独立训练/测试 CSV 工作区，以及不落库的本地 CSV 预处理。旧文档中与现行页面不一致的操作说明，以本索引中的用户手册和当前软件界面为准。
