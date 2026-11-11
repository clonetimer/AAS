# AAS 双坐标版本：Windows 从零部署

> 本文是源码/开发环境部署说明，需要准备 Python 并安装 requirements.txt。普通用户的全离线安装请使用独立 Windows 离线包，参见 [离线部署说明](docs/WINDOWS_OFFLINE_DEPLOYMENT.md) 和包内「部署说明.md」。

## 环境与安装

需要 Windows、Python 3、PowerShell 和可访问 `requirements.txt` 中依赖的安装源。交付 ZIP 含完整源码与部署脚本，不含 Python 虚拟环境、第三方 wheel 或历史遥测数据库；离线环境须预先准备依赖。

解压 ZIP，进入 `AAS` 目录。在 PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_dual_coordinate_windows.ps1
.\.venv\Scripts\python.exe scripts\visualization_server.py --host 127.0.0.1 --port 8765
```

脚本会建立虚拟环境、安装依赖。若 `datasets/source_csv_gz/*.csv.gz` 与 `datasets/source_labels/targets.zip` 同时存在，它调用 `scripts.rebuild_target_labeled_v2` 从原始遥测和标签构建 V2 库；两者都不存在时建立空 V2 库；只存在一类时拒绝继续，避免误建空库。它不会下载历史遥测数据。也可通过用户 CSV 工作区导入数据。

2026-11-04 的历史离线包采用同一建库入口：先复制 19 份 `.csv.gz` 和 `targets.zip`，再运行 `python -m scripts.rebuild_target_labeled_v2`。该包本身没有遥测 SQLite 数据库。脚本也识别旧文件名 `datasets/aas_telemetry.sqlite`：保留原件，将副本迁移到当前默认的 `aas_telemetry_v2.sqlite`。

如果已有 V2 历史库，先停止服务，再执行：

```powershell
.\scripts\install_dual_coordinate_windows.ps1 -DatabaseSource "D:\path\old_v2.sqlite"
```

脚本复制来源文件到 `datasets`，在迁移前生成时间戳备份，并对安装目录内的副本执行增量迁移。若目标数据库已存在，脚本拒绝覆盖。已有安装库可停服后手工备份并执行 `python -m scripts.migrate_v2_coordinates --db datasets\aas_telemetry_v2.sqlite --apply`。

## CSV 数据模式

- 时间序列：保留原 `time_s` 秒制 CSV 导入与对齐方式。
- 帧序列：选择无时间列 CSV；优先读取显式 `frame` 列。没有 `frame` 列时须确认 CSV 原始行序代表从 1 开始的帧号。时间列不存在时，不生成时间戳。
- 训练 CSV 仅接受真实全正常 0 标签，或经人工确认的全正常数据。带 0/1 标签的独立测试 CSV 走监督评估；完全无标签测试 CSV 走仅推理，不产生监督指标。
- 两种坐标模式不能混选。帧模式模型契约保存 `mode=frame`、`frame_origin=1`、`alignment=frame_index`、`frame_sequence_policy=independent`。

每个导入 CSV 保存原始 SHA-256。CSV 工作区与历史数据库分开存储。帧号缺口在预检质量摘要中单独列出；参量缺测保留为 SQL NULL。训练帧序列有缺失帧时须先纠错。

## 升级后核对

停止服务后保存旧库的文件备份。运行迁移命令时核对输出的 `before`/`after` 记录数、标签分布和旧字段逻辑 SHA-256。启动服务，分别检查历史时间序列和新帧序列目录；执行一次时间模式训练与一组帧模式训练、监督评估、仅推理。若真实历史库未提供，不能视为 HIS-02 已通过。

## 按数据源清理

在“历史故障数据库”页面选择数据库，点击“管理数据源”，选择来源并预检。预检列出分片、采样数及模型/派生数据引用；只有无阻塞引用时，输入完整来源名称才能提交删除。服务运行中如有任务正在执行，会拒绝删除。删除在一个 SQLite 事务中清理该来源的采样、分片、标签、导入记录和独占参量，保留其他来源、共享参量及原始 CSV 文件。删除审计保留来源名称、哈希和删除数量。

删除不可撤销。操作前停服备份目标 V2 数据库；已有模型引用的来源不能直接删除。删除来源不会删除外部 `.csv.gz` 或模型文件，SQLite 文件大小也不会立即缩小；需要回收磁盘空间时，可在停服备份后另行执行 SQLite `VACUUM`。

## 本地 CSV 预处理与故障模拟导出

在“数据预处理 → 本地 CSV”选择 CSV/CSV.GZ，检查字段，选数值参量、用途和方法，点击“生成预处理文件”，再点击“导出预处理后 CSV”。导出文件保留原始列，另加处理后数值和质量标记；本地 CSV 预处理不写入历史库。文件上限为 8 MB、50,000 行，缺测可用 Hampel + 有限前向填充处理，测试用途不能重新拟合 Z-score。历史库预处理生成的派生参量可在下方记录表逐条导出。

在“模拟故障数据库”选择注入后的版本，点击“导出当前版本 CSV”；用于算法的无表格转义格式可选“导出当前版本（算法用）”。历史库故障注入记录也有“导出注入后 CSV”入口。
