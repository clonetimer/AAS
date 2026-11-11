# AAS P0 Windows 离线包：本地验收步骤

构建日期：2026-11-11  
离线包：`AAS_WINDOWS_OFFLINE_P0_20261111_R1.zip`  
源码基线：`7a787f44b80ce671e61786221e3be4a39b48d80b`  
文件大小：389,969,088 bytes  
SHA-256：`155583ee81513e2c1dcf1dfecde083deb3282f795c24072fa799b1b0b97510ba`

## 1. 交付包内容核验

- ZIP CRC：PASS
- 清单逐文件 SHA-256：PASS
- 清单文件：75
- wheelhouse：39 个 wheel；锁定依赖：39 项
- 示例遥测：19 个 CSV.GZ
- 默认不包含 evaluated 实验模型、model.db、pickle、joblib 和 .bak 文件
- 源码提交：`7a787f44b80ce671e61786221e3be4a39b48d80b`

## 2. 拼接分卷

将以下文件放在同一个目录：

- `AAS_WINDOWS_OFFLINE_P0_20261111_R1.part01`
- `AAS_WINDOWS_OFFLINE_P0_20261111_R1.part02`
- `AAS_WINDOWS_OFFLINE_P0_20261111_R1.part03`
- `AAS_WINDOWS_OFFLINE_P0_20261111_R1.part04`
- `AAS_WINDOWS_OFFLINE_P0_20261111_R1.part05`
- `Join-AAS-P0-OfflinePackage-R1.ps1`

在 PowerShell 5.1 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Join-AAS-P0-OfflinePackage-R1.ps1
```

脚本生成 ZIP 后会自动计算 SHA-256。期望值必须为：

```text
155583ee81513e2c1dcf1dfecde083deb3282f795c24072fa799b1b0b97510ba
```

也可以单独核验：

```powershell
Get-FileHash .\AAS_WINDOWS_OFFLINE_P0_20261111_R1.zip -Algorithm SHA256
```

## 3. Windows 10/11 干净安装

建议在未安装过 AAS 的 Windows 10/11 x64 电脑或独立测试目录执行。

1. 确保目标磁盘至少有约 35 GB 可用空间（使用随包示例数据初始化数据库时）。
2. 解压完整 ZIP，不要单独移动包内文件。
3. 右键 `一键部署.cmd`，选择运行；通常不需要管理员权限。
4. 安装器应先检查 SHA-256、wheelhouse 和磁盘空间。
5. 双击部署后会询问安装目录，并优先建议有足够空间的非系统盘；也可直接输入自定义路径，例如 `E:\AAS`。
6. 安装完成后，双击安装目录内的 `Start-AAS.cmd`。
7. 浏览器访问 `http://127.0.0.1:8765`，完成首次管理员账号初始化。

若要指定安装目录，可在解压目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Deploy-AAS.ps1 -TargetDir 'E:\AAS'
```

## 4. 功能验收清单

### A. 安装与启动

- [ ] 首次安装成功，无需访问 PyPI 或其他网络依赖源
- [ ] 安装器能识别磁盘空间不足并在写入前终止
- [ ] 安装目录中包含启动、停止、卸载快捷程序
- [ ] 首次访问可以创建本机管理员账号
- [ ] 页面静态资源、图标和七个首页入口正常显示

### B. 数据与业务

- [ ] 历史遥测数据库能够初始化并打开
- [ ] 历史数据库查询和曲线浏览正常
- [ ] 故障模拟页面正常，CSV 导入和故障注入可用
- [ ] 预处理页面可以导入本地 CSV 并导出结果；不应写入历史库
- [ ] 训练/测试 CSV 可独立导入、持久保存和主动删除；不应写入历史遥测数据库
- [ ] 任务引用未释放时，删除训练/测试 CSV 应被阻止
- [ ] 算法管理、训练/评估和结果报表页面可正常打开
- [ ] 无标签 CSV 只输出推理结果，不应伪造真值或监督指标

### C. 服务生命周期

- [ ] 关闭浏览器后服务仍按预期运行
- [ ] `Stop-AAS.cmd` 能停止服务
- [ ] `Start-AAS.cmd` 能重新启动服务
- [ ] 重启后历史库、用户 CSV 工作区和模型仍然存在
- [ ] 服务停止接口仅接受本机回环请求及有效控制令牌

### D. 卸载与恢复

- [ ] 运行 `Uninstall-AAS.cmd`，输入 `REMOVE` 确认
- [ ] 默认卸载将 datasets、models、configs 迁移到同级备份目录
- [ ] 备份目录存在且数据库/用户 CSV 文件仍可读取
- [ ] 使用备份数据重新部署后能够恢复访问
- [ ] 未显式指定 `-DeleteUserData` 且再次输入 `DELETE DATA` 时，不应删除用户数据

## 5. 数据迁移专项（可选，使用数据库副本）

不要直接对唯一的生产数据库做测试。

1. 先停止旧 AAS 服务。
2. 复制旧数据库到测试目录。
3. 使用新的空安装目录，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Deploy-AAS.ps1 -TargetDir 'E:\AAS-Migration-Test' -DatabaseSource 'E:\AAS-Test-Backup\aas_telemetry_v2.sqlite'
```

4. 验证新目录中的数据库可打开、迁移检查通过。
5. 比对原始数据库 SHA-256，确认原文件没有被修改。

## 6. 请回报的验收证据

请记录：

- Windows 版本（Win10/Win11）、系统架构
- 安装方式及安装目录
- 安装日志中 SHA-256、wheelhouse、数据库检查结果
- 安装成功/失败截图
- 首页、历史库、故障模拟、预处理、训练评估页面截图
- 启动/停止/重启结果
- 卸载后备份目录结构及关键数据库文件大小
- 失败时的 `%TEMP%\AAS_Deploy_*.log` 和服务日志

## 7. 验收边界

GitHub Actions 的 Linux 与 Windows Runner 合约测试、PowerShell 5.1 脚本解析已通过；这些结果不等同于真实 Windows 桌面环境中的完整安装验收。正式 Stable 发布仍以本地 Windows 10/11 实机验收通过为前提。
