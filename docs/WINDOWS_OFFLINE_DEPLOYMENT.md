# Windows 离线部署与交付说明

## 1. 适用范围

此说明针对完整 Windows 10/11 x64 离线交付包。它与仓库源码安装不同：离线包包含锁定版本的 Python 3.8 运行环境、wheelhouse、AAS 源码和可选示例遥测数据，不需要访问 PyPI。

当前离线基线为 CPython 3.8 ABI。该基线用于保持现有兼容性，不代表推荐长期停留在 Python 3.8；Python 3.11/3.12 迁移应作为独立版本升级，重新构建和验收依赖。

## 2. 用户安装

1. 将完整 ZIP 解压到本机可写目录。
2. 双击 `一键部署.cmd`，或在 PowerShell 中运行 `Deploy-AAS.ps1`。
3. 默认安装目录为 `%LOCALAPPDATA%\AAS`。可通过 `-TargetDir 'E:\AAS'` 指定目录。
4. 若使用包内示例遥测建库，安装器会要求约 35 GB 可用空间；如果空间不足，应选择其他磁盘。迁移已有数据库时，空间要求会根据旧库大小动态估算。
5. 安装器在修改目标目录前校验 SHA-256 清单；检查 Python ABI、wheelhouse 数量和依赖一致性。SHA-256 清单用于发现文件损坏或错配，不是数字签名；不能抵抗清单与文件同时被恶意替换。正式分发应使用受控仓库/发布渠道，并保留源码提交 SHA。
6. 完成后双击安装目录中的 `Start-AAS.cmd`。浏览器打开本机首页并初始化管理员账号。

安装器不会覆盖已存在的 AAS 安装目录。升级请使用新的目标目录，完成数据迁移和验收后再处理旧安装。

## 3. 安装后的快捷操作

| 文件 | 作用 |
| --- | --- |
| `Start-AAS.cmd` | 启动本机服务并打开浏览器 |
| `Stop-AAS.cmd` | 通过本机随机令牌控制接口请求服务正常停止 |
| `Uninstall-AAS.cmd` | 卸载程序；默认保留用户数据 |
| `Uninstall-AAS.ps1 -DeleteUserData` | 在明确确认后删除用户数据 |

受控停止接口只绑定并接受本机回环请求，并校验当前服务进程 PID、端口和随机令牌。若服务不是通过快捷方式启动，通常应在原启动终端按 Ctrl+C 停止。

## 4. 包内数据和模型策略

离线包默认携带示例遥测 CSV.GZ 和标签文件，供首次建库和体验功能使用。

**默认不携带任何预置模型。** 之前的五个模型属于部署/对照实验资产，状态为 evaluated 而非 published，且评估记录存在 Recall=0、F1=0、ROC-AUC/PR-AUC 未计算等问题；其中一个实验明确标记含标签泄漏。它们不应被展示为可直接使用的正式模型。

用户应使用自己的数据训练模型，并在独立评估后决定是否发布。模型导入仍应使用系统提供的受控模型包，不要手工复制不明来源的 pickle 文件。

## 5. 用户数据保护

- 安装器只接受新的目标目录，不覆盖已有安装。
- 安装失败时，如果目标目录由本次安装新建，安装器会清理本次产生的目标目录；部署日志保留在系统临时目录。
- 卸载默认将 `datasets/`、`models/` 和用户配置移动到同级的 `AAS_UserDataBackup_时间戳` 目录。
- 删除用户数据需要额外显式确认。
- 备份 SQLite 数据库前先停止 AAS 服务。

## 6. 交付验收边界

源码单元测试、静态检查和离线包哈希校验不能代替 Windows 实机验收。正式交付前，仍需在干净的 Windows 10/11 x64 环境完成：
1. 无 Python、无网络环境的一键安装。
2. Python 便携版回退路径。
3. 离线依赖安装和 `pip check`。
4. 示例数据库建库、SQLite 完整性检查和服务 HTTP 启动。
5. 首页登录、历史库浏览、故障模拟、预处理、训练/评估 CSV 工作区和结果报表。
6. 服务停止后重启，验证历史库、用户 CSV 工作区和模型仍然存在。
7. 默认卸载保留用户数据；显式删除选项的二次确认。

未完成以上实机步骤前，不应将候选包标记为 Windows 正式验收通过。


## 7. 维护者如何重建离线包

从当前源码工作树构建时，先解压上一版已校验的离线包作为 payload seed。seed 只提供 Python 安装程序、便携运行时、pip、锁定 wheelhouse、示例遥测和标签；不会复用 seed 内的旧源码、旧部署脚本或模型。

```powershell
python scripts/build_windows_offline_package.py `
  --source-root . `
  --seed-root 'D:\AAS_UI_CLEAN_OFFLINE_20261109_extracted' `
  --output 'D:\AAS_UI_CLEAN_OFFLINE_candidate.zip'
```

若源码来自 GitHub ZIP 而不是 Git clone，需要显式传入该 ZIP 对应的 40 位提交 SHA：

```powershell
python scripts/build_windows_offline_package.py --source-root . --seed-root 'D:\AAS_UI_CLEAN_OFFLINE_20261109_extracted' --source-commit '<40位源码提交SHA>' --output 'D:\AAS_UI_CLEAN_OFFLINE_candidate.zip'
```

构建器会校验锁定依赖与 wheelhouse 精确匹配，排除默认模型和备份文件，生成新源码 ZIP 与 SHA-256 清单，并在输出后重新校验 ZIP CRC、文件大小和 SHA-256。需要替换已存在输出文件时，显式增加 `--overwrite`。
