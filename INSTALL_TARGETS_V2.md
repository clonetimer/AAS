> **Phase 15 更新**：完整源码不再随包附带19份原始 `.csv.gz`。下述建库命令必须增加 `--input-dir "你的本地CSV目录"`；已有有效 V2 数据库可直接使用。详见 `PHASE15_DELIVERY.md`。

# AAS V2：19 份遥测与 targets.zip 标注整合版

这是一份全量源码，不需要 Phase 1～7 的任何增量包。

## 一、安装

解压完整源码包。`AAS/datasets/` 下放置唯一有效的 `aas_telemetry_v2.sqlite`。

**方式 A（推荐，下载快）**：使用你本地已有的 19 份 `.csv.gz` 和随源码附带的原始 `targets.zip`，新建已标注数据库：

```powershell
cd AAS
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.rebuild_target_labeled_v2 --input-dir "D:\Datasets\source_csv_gz" --output datasets\aas_telemetry_v2.sqlite --report targets_annotation_report.json
.\.venv\Scripts\python.exe scripts\visualization_server.py --host 127.0.0.1 --port 8765
```

**方式 B（无需重新计算 900 多万条数据）**：下载四个已标注数据库分卷放入 `AAS` 目录。确保路径是 `AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst.part00` ～ `.part03`。在 Windows PowerShell 中从 `AAS` 目录运行：

```powershell
cmd /c copy /b AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst.part00+AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst.part01+AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst.part02+AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst.part03 AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst
.\.venv\Scripts\python.exe scripts\install_phase7_database.py --archive AAS_Phase7_targets_labeled_v2_20261020.sqlite.zst --destination datasets\aas_telemetry_v2.sqlite
```

安装器验证已标注数据库的完整性、样本标签数量、19 次运行及 SHA-256，默认拒绝覆盖已有 SQLite。该版本为**已标注数据库**，不要安装此前 Phase 7 尚未标注的数据库。

注意：若目标数据库已经存在，务必先停止服务并做人工备份；重建器默认拒绝覆盖，**不要和旧版本同时保留两套活动数据库**。

## 二、数据语义

1. `targets.zip` 由 3,548 个 JSON 组成，其中 19 个 token 与 19 份 `.csv.gz` 一一匹配。其余 3,529 条不属于当前数据，不导入。
2. 按文件名排序分别创建 `SIM-SAT-001` ～ `SIM-SAT-019` 虚拟**型号别名**。这是用于区分 19 次仿真运行的工程数据身份，不是实际型号鉴定。
3. 各运行的 `time_s` 是独立的经过时间。跨运行同为 100 秒不表示同时发生，不允许自动拼接。
4. `v2_samples.label_int` 中 0 表示运行正常状态；故障运行 `time_s >= onset_s` 为 1。`targets.role=FAULT` 不等于整条曲线都应标 1。
5. 故障部件依据 `targets.component` 提供的**故障注入源**做分系统归属；每个遥测字段的测量部件另行在 `v2_channels` 登记。不能因为整次运行有故障就判定每个通道均有独立硬件故障。
6. `v2_run_annotations` 保留 `split`、`group`、故障类型与 onset；`v2_run_fault_locations` 保存专家语义映射。这些是监督真值/审计元数据，**不得进入模型特征矩阵**。
7. 仍沿用 V2 `role=simulation`，不把 train split 自动改写成整个参量的 `train` 用途。Phase 8 已实现独立的运行级选择：完整 `train+NORMAL` 运行可用于当前无监督检测器训练；`validation` 运行用于留出评估，保留原始 split/group 防止泄漏。详见 `INSTALL_PHASE8.md`。

## 三、界面

进入「数据浏览」，选择用途「仿真」，通过卫星型号 `SIM-SAT-xxx`、分系统、部件、参量筛选。曲线点悬浮显示 0/1 状态标签；故障运行的已知 onset 用半透明红色区间展示，悬浮说明注入部件；这属于**运行级事件真值**，不是模型的报警，也不是每个通道自身故障的证据。

## 四、版本与验收边界

只在本次提供的 19 次仿真上建立监督标注。历史数据库不包含在本交付物中。新增后端验证覆盖 19 个 token 关联、56 个参数字段身份、来源与时钟隔离、故障前/后边界及可视化事件查询。未经 Windows 实机浏览器和全部业务用例验收的版本仍为 Candidate。
