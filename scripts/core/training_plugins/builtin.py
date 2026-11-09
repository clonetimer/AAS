"""内置训练策略。"""

from __future__ import annotations

from .base import BaseTrainingStrategy
from .registry import register_strategy


class StandardTrainingStrategy(BaseTrainingStrategy):
    name = "standard"
    label = "标准训练策略"
    description = "算法执行 → 指标计算 → 模型保存（与原有流程一致）"

    def execute(self, pipeline, algorithm, data_bundle, algorithm_params, experiment_id):
        return pipeline.run_core(algorithm, data_bundle, algorithm_params, experiment_id)


register_strategy(StandardTrainingStrategy())
