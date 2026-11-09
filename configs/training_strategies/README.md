# 自定义训练策略（插件）

把一种新的训练编排策略做成插件（如交叉验证 / 集成 / 分阶段训练），只需两步，
**无需改动 TrainingPipeline 的核心流程**：

1. 编写策略类（继承 `scripts.core.training_plugins.base.BaseTrainingStrategy`）：

```python
from scripts.core.training_plugins.base import BaseTrainingStrategy

class CvStrategy(BaseTrainingStrategy):
    name = "cv"                       # 策略键名
    label = "交叉验证"
    description = "K 折交叉验证训练"

    def execute(self, pipeline, algorithm, data_bundle, algorithm_params, experiment_id):
        # 可自由编排：多次调用 pipeline.run_core / 自定义评估，再返回三元组
        algorithm_result, metrics, model_info = pipeline.run_core(
            algorithm, data_bundle, algorithm_params, experiment_id
        )
        return algorithm_result, metrics, {**model_info, "cv": True}
```

2. 在 `configs/training_strategies/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: cv
label: 交叉验证
description: K 折交叉验证训练
class_path: my_package.my_module.CvStrategy
dependencies: []
```

放置后该策略自动注册；在调用 `TrainingPipeline.run(..., strategy="cv")` 时启用。
这复用了与检测器（detector）完全相同的插件范式。
