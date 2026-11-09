# AAS V2 数据集

- `aas_telemetry_v2.sqlite`：唯一活动的遥测数据库。**完整源码包不含大体积数据库**，可安装已标注的预构建库，或执行下面的建库命令。
- `source_csv_gz/`：**精简源码包不附带**；你本地持有19次仿真原始 `.csv.gz`，只有重建库时才需通过 `--input-dir` 指定其目录。
- `source_labels/targets.zip`：用户提供的运行级正常/故障监督标注原件；3,548 份 JSON 中只有 19 份与当前 CSV 匹配，不导入其余记录。
- `templates/`：V2 原生导入/事件模板。

## 首次创建全新已标注数据库

从 `AAS/` 下执行（目标已存在则**拒绝覆盖**）：

```powershell
.\.venv\Scripts\python.exe -m scripts.rebuild_target_labeled_v2 --input-dir "D:\Datasets\source_csv_gz" --output datasets\aas_telemetry_v2.sqlite --report targets_annotation_report.json
```

建库后：19 个模拟卫星型号别名 `SIM-SAT-001`～`SIM-SAT-019`（**非真实卫星型号**），19 个独立的 `elapsed_run:<token>` 数字时间基准，1,064 个逻辑参量，9,152,248 条 0/1 标注样本。

`targets.zip` 的 `role` 与 `onset_s` 提供**运行级故障状态**。对于故障运行，采样 `time_s < onset_s` 标 0，`time_s >= onset_s` 标 1；正常运行全部标 0。这表示仿真运行进入故障状态，不表示该运行的每一只传感器都发生局部故障。

`split`（train/validation）和 `group` 保存在专用标注表中，不会被当作模型输入，也**不自动改写** V2 的 `role=simulation`。尤其 train split 可能包含故障运行，不等价于现有无监督检测器仅正常数据的训练入口。
