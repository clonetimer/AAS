# 🛠️ 工具脚本

本目录包含配置、实验管理、输出处理的辅助脚本。

## 📋 脚本列表

### `clean_outputs.py` - 输出目录清理

根据条件清理 `outputs/` 目录中的实验结果，释放磁盘空间。

**功能:**
- 按数量保留（保留最新 N 个实验）
- 按时间保留（保留最近 N 秒内的实验）
- 干运行模式（预览将删除的内容）
- 显示每个实验的大小与创建时间

**用法:**

```bash
# 保留最新的 10 个实验，删除其他
python scripts/clean_outputs.py --keep-latest 10

# 保留最近 24 小时（86400 秒）的实验
python scripts/clean_outputs.py --keep-recent 86400

# 预览将删除的内容，不实际删除
python scripts/clean_outputs.py --keep-latest 5 --dry-run

# 删除所有输出
python scripts/clean_outputs.py --cleanup-all

# 使用自定义输出目录
python scripts/clean_outputs.py --output-dir /path/to/outputs --keep-latest 5
```

**输出示例:**

```
📁 输出目录: outputs
📊 发现 12 个实验

📊 实验总览:
实验ID                    创建时间                    大小 (MB)    状态      
------------------------------------------------------------------------
exp_20260408_104349       2026-04-08 10:43:49         45.23       🗑️  待删
exp_20260408_112519       2026-04-08 11:25:19         48.92       🗑️  待删
...
exp_20260408_214756       2026-04-08 21:47:56         52.17       ✅ 保留
------------------------------------------------------------------------
总大小: 623.45 MB | 将删除: 320.50 MB | 将保留: 302.95 MB

⚠️  确认删除 7 个实验? (y/N): y
🧹 清理计划: 将删除 7 个实验目录
  ✓ 已删除 exp_20260408_104349
  ✓ 已删除 exp_20260408_112519
...
✅ 清理完成: 删除了 7 个目录
```

**选项:**

| 选项 | 说明 |
|------|------|
| `--output-dir PATH` | 输出目录路径 (默认: outputs) |
| `--keep-latest N` | 保留最新的 N 个实验 |
| `--keep-recent SECONDS` | 保留最近 N 秒内创建的实验 |
| `--cleanup-all` | 删除所有实验 |
| `--dry-run` | 预留运行，不实际删除 |

### `build_historydb_sample.py` - 样例历史库构建

把 `datasets/templates/telemetry_template.csv` 和 `datasets/templates/fault_intervals_template.csv` 合成为 SQLite 历史库，供后续评估实验直接使用。

**用法:**

```bash
python scripts/build_historydb_sample.py --telemetry datasets/templates/telemetry_template.csv --fault-intervals datasets/templates/fault_intervals_template.csv --output datasets/history_sample.db
```

**结果:**

- 生成 `datasets/history_sample.db`
- 表名默认是 `history_data`
- `label` 会按故障区间自动写入

### `build_historydb_manifest.py` - JSON 清单导入

从 JSON 清单导入故障记录和遥测数据，生成结构化历史库。

**用法:**

```bash
python scripts/build_historydb_manifest.py --manifest datasets/templates/fault_manifest_template.json --output datasets/history_manifest.db
```

**结果:**

- 生成 `dataset_meta`、`fault_records` 和 `history_data` 三张表
- 支持故障记录的 JSON 导入
- 支持可选的遥测路径覆盖
- 当前可直接运行的样例清单： [datasets/templates/fault_manifest_sample.json](../datasets/templates/fault_manifest_sample.json)

### `visualization_server.py` - 可视化交互界面

启动本地可视化交互面板，拆分为三个独立入口：数据浏览、算法运行和结果报表。

**用法:**

```bash
python scripts/visualization_server.py --port 8765
```

打开浏览器访问：

```text
http://127.0.0.1:8765
```

页面入口：

- `browser.html`：数据浏览
- `runner.html`：算法运行
- `reports.html`：结果报表

**功能:**

- 浏览 `datasets/*.db` 历史库
- 查看表结构、曲线和故障记录
- 运行 `configs/experiments/*.yaml` 评估配置
- 查看 `outputs/*/reports/summary.json` 最近结果

---

## 🚀 常见任务

### 任务 1：定期清理输出目录

保留最新的 20 个实验：

```bash
python scripts/clean_outputs.py --keep-latest 20
```

### 任务 2：删除超过 7 天的数据

```bash
python scripts/clean_outputs.py --keep-recent 604800  # 7 * 24 * 3600
```

### 任务 3：预览清理效果

```bash
python scripts/clean_outputs.py --keep-latest 10 --dry-run
```

### 任务 4：批量运行多个实验

创建脚本 `batch_run.sh`:

```bash
#!/bin/bash
for config in configs/experiments/*.yaml; do
    echo "运行实验: $config"
    python main.py --config "$config"
done
```

然后执行：

```bash
bash batch_run.sh
```

---

## 📊 监控与统计

### 查看输出目录大小

```bash
python scripts/clean_outputs.py --dry-run | grep "总大小"
```

### 导出实验统计

```bash
python scripts/clean_outputs.py --dry-run > experiment_stats.txt
```

---

## 🔗 关联文档

- [配置管理](../configs/README.md)
- [快速开始](../README.md#快速开始)
- [操作手册](../docs/runbook/)
