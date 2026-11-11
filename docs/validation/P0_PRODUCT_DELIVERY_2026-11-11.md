# P0 产品交付整改记录（2026-11-11）

## 基线与原则

- 代码基线：GitHub main，提交 8d19e391d2de8fa08e6327dc38c9e965964fe394。
- P0 候选源码：feat/aas-p0-product-delivery，提交 2f6ca2b94e02f3d1149143e7b73a3072acb75acf。
- 目标：不推倒现有 AAS 架构，优先收敛普通用户安装、数据去向、首页入口、文档和安全卸载。
- 数据原则：历史遥测、故障模拟、训练/测试用户 CSV 保持三个独立存储边界；用户数据不随源码包或普通安装包被覆盖。
- 模型原则：默认 Windows 离线包不携带开发阶段实验模型，不把 evaluated 状态误作 published。

## 本轮改动

1. **源码与离线包版本治理**
   - 新增 scripts/build_windows_offline_package.py。
   - 从指定 Git 源码树构建程序/AAS_源码.zip，记录 40 位源码提交 SHA。
   - 从已校验的离线种子包仅复用 Python 运行时、锁定 wheelhouse、示例遥测和标签。
   - 构建前逐项核验种子包原始清单、文件大小和 SHA-256，并拒绝未列入清单的额外文件。
   - 校验 39 个 wheel 的 ZIP CRC、唯一 dist-info/METADATA，以及文件名与内部 Name/Version 一致。
   - 构建时排除模型数据库、pickle 模型、备份配置、运行数据库和缓存。
   - 生成新 SHA-256 清单，并在构建后重新读取 ZIP 校验 CRC、文件大小和 SHA-256。

2. **预置模型治理**
   - 默认构建过程不复制 数据/模型。
   - 保留用户训练、发布和受控模型包导入能力。
   - 不把五个 evaluated 部署/对照模型作为正式预训练模型分发。

3. **文档纠偏**
   - 修订 README、文档索引和 CSV 导入向导。
   - 明确训练/测试 CSV 保存于 datasets/user_csv_workspaces/user_csv_workspace.sqlite，不写入历史遥测数据库。
   - 新增普通用户快速入门和 Windows 离线部署说明。
   - 区分源码/开发安装与完整离线安装。

4. **首页功能入口**
   - 首页快速入口补齐历史遥测、故障模拟、数据预处理、训练评估、实验结果、算法管理和性能监控。
   - 入口编号连续，全部指向实际页面；未登录时沿用首页登录引导。
   - 修正任务状态卡片的 HTML 闭合结构和过期说明。

5. **Windows 部署与维护工具**
   - 新增离线安装脚本：安装前 SHA-256 全量校验、Python ABI 与 wheelhouse 数量检查、磁盘空间预检。
   - 默认安装路径改为当前用户 LocalAppData，不再假定 D 盘存在。
   - 安装器拒绝覆盖已有目录；本次新建目录安装失败时清理本次目录，部署日志保留在系统临时目录。
   - 新增启动、受控停止和卸载快捷方式。
   - 停止接口只接受本机回环请求，并校验服务 PID、端口和随机令牌。
   - PowerShell 脚本统一采用 UTF-8 BOM，确保 Windows PowerShell 5.1 正确解析中文脚本。
   - 卸载前检查 AAS 服务进程，不再只检查默认 8765 端口；默认保留 datasets/models/configs 到同级备份目录，删除用户数据需要二次输入确认。

## 自动化测试与构建结果

- GitHub Actions 最新 PR 验证：https://github.com/clonetimer/AAS/actions/runs/42964101281
- Linux Python P0 测试：**18 passed**。
- Windows Python 3.8 / PowerShell 5.1 P0 测试：**19 passed**。
- Windows PowerShell 5.1 解析：**4/4 脚本通过**。
- 可复现源码归档：**601 个文件**，AAS_源码.zip 为 1,665,445 bytes；源码提交 SHA 为 2f6ca2b94e02f3d1149143e7b73a3072acb75acf。
- GitHub Actions 源码归档产物：https://github.com/clonetimer/AAS/actions/runs/42964095806（保留期 14 天）。
- 完整离线候选包已在隔离构建环境生成：**389,964,899 bytes**，SHA-256：2c7b9cec5bb7df279a0bcfe00b279e5845f1f40685e3129665bea523e7550f47。
- 候选包清单包含 75 个文件；ZIP CRC、全部清单 SHA-256、文件大小检查通过；未发现模型目录、pickle 模型、数据库、.bak、.pyc 或 __pycache__；models_included=false。
- 候选包较原始 398,428,340 bytes 离线包减少约 8.46 MB，主要来自移除实验模型和备份配置；未削减 PyTorch、SciPy 等运行依赖。

## 当前验收边界

自动化测试和候选包完整性检查已通过，但它们不能替代目标机器上的端到端安装验收。当前 PR 仍为 Draft，尚未合并 main，也未创建正式 Release。

Windows 10/11 实机安装、浏览器功能、数据库建库、服务生命周期及卸载保留仍待目标环境验收。正式交付前必须完成以下清单：

## Windows 实机验收清单

- [ ] 干净 Windows 10 x64、无 Python、无网络：一键安装成功。
- [ ] 干净 Windows 11 x64、无 Python、无网络：一键安装成功。
- [ ] Python MSI 路线与便携运行时路线分别通过。
- [ ] SHA-256 校验失败时，安装器在写入目标目录前中止。
- [ ] 磁盘不足时给出明确提示，不留下半安装目录。
- [ ] wheelhouse 离线安装、pip check 通过。
- [ ] 示例数据建库、SQLite 完整性和双坐标检查通过。
- [ ] 首次管理员初始化、七个首页入口均可访问。
- [ ] 训练/测试 CSV 独立持久化；重启后仍可选；任务引用期间不能删除。
- [ ] 故障模拟多次叠加、版本导出和结果查询通过。
- [ ] Stop-AAS 正常停止；重新启动后数据库和模型仍存在。
- [ ] 默认卸载保留用户数据；显式删除数据需二次确认。
