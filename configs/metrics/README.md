# 自定义评估指标（插件）

把一个新的评估指标做成插件，只需两步，**无需改动核心代码**：

1. 编写指标类（继承 `evaluator.metrics_plugins.base.BaseMetric`）：

```python
from evaluator.metrics_plugins.base import BaseMetric

class MCCMetric(BaseMetric):
    name = "mcc"                 # 指标键名（结果字典的 key）
    label = "MCC"                # 展示名
    description = " Matthews 相关系数"
    requires = {"labels", "preds"}   # 该指标需要的输入

    def compute(self, ctx):
        tp, fp, fn, tn = ctx.confusion()
        denom = ((tp+fp)*(tp+fn)*(tn+fp)*(tn+fn)) ** 0.5
        return (tp*tn - fp*fn) / denom if denom else 0.0
```

2. 在 `configs/metrics/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: mcc
label: MCC
description: Matthews 相关系数
class_path: my_package.my_module.MCCMetric
dependencies: []      # 需要的第三方库（如 sklearn），缺失时自动置灰
```

放置后该指标会自动注册，出现在评估结果中。这复用了与检测器（detector）完全相同的
插件范式：契约基类 + 注册表 + YAML 自动发现 + 依赖检测。
