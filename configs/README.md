# 系统配置

`configs/` 包含检测算法、数据源、指标、预处理、报告、执行器、训练策略和故障类型的描述文件，以及 CLI 实验配置。

| 目录 | 用途 |
|---|---|
| `detectors/` | 检测器元数据、参数和依赖信息 |
| `datasources/` | 数据源插件 |
| `metrics/` | 评估指标插件 |
| `preprocessors/` | 预处理算子 |
| `reports/` | 报告输出格式 |
| `runners/` | 算法执行后端 |
| `training_strategies/` | 模型训练策略 |
| `fault_types/` | 故障模拟类型 |
| `experiments/` | CLI 实验配置 |

## CLI 示例

```bash
python main.py --config configs/experiments/experiment.yaml
```

修改插件 YAML 前应确认类路径、参数 Schema 和依赖与实际插件一致。检测器的配置格式详见 [检测器插件说明](detectors/README.md)，数据导入列名别名由 `configs/data_import_aliases.yaml` 管理。

本地管理员凭据文件 `.aas_admin.json` 在首次设置管理员时由 Web 服务自动生成。文件包含密码哈希，属于运行时敏感数据，请勿提交版本库或放入交付压缩包。
