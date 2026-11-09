# 自定义预处理算子（插件）

把一种新的预处理步骤做成插件（如 FFT 去噪、小波变换、去趋势），只需两步，**无需改动 PreprocessingManager**：

1. 编写算子类（继承 `scripts.core.preproc_plugins.base.BasePreprocessOperator`）：

```python
from scripts.core.preproc_plugins.base import BasePreprocessOperator

class ShiftOperator(BasePreprocessOperator):
    name = "shift"                  # 算子键名（pipeline 中引用）
    label = "平移"
    description = "将 value 整体 +1"

    def enabled(self, config):
        return config.get("shift", False)   # 由 pipeline_config 控制是否启用

    def apply(self, rows, config):
        for r in rows:
            if r.get("value") is not None:
                r["value"] = r["value"] + 1
        return rows
```

2. 在 `configs/preprocessors/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: shift
label: 平移
description: 将 value 整体 +1
class_path: my_package.my_module.ShiftOperator
dependencies: []
```

放置后该算子自动注册，可通过 `pipeline_config` 中的 `shift: true` 启用。这复用了与检测器（detector）、
评估指标（metric）、数据源（datasource）完全相同的插件范式：契约基类 + 注册表 + YAML 自动发现。
