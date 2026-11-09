"""训练策略插件契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTrainingStrategy(ABC):
    """训练策略插件契约。

    ``execute`` 接收训练管线实例与必要参数，负责「算法执行 → 指标计算 → 模型保存」
    这一段的编排，返回 ``(algorithm_result, metrics, model_info)``。内置 ``standard``
    策略即复刻原有流程；新增策略（如交叉验证）可在此扩展而不改动 TrainingPipeline。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    dependencies: list[str] = []

    @abstractmethod
    def execute(self, pipeline, algorithm, data_bundle, algorithm_params, experiment_id):
        raise NotImplementedError
