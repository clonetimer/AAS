"""预处理算子插件体系（与 detectors 同构）。

- ``base.BasePreprocessOperator``：算子插件契约（apply(rows, config)）。
- ``registry``：注册表 + 默认管线 DEFAULT_PIPELINE + run_pipeline + YAML 自动发现。
- ``builtin``：内置 4 个算子（fill_missing / repair_outlier / smooth / normalize）。

新增一种预处理算子只需写一个子类 + 一个 YAML 描述，无需改动 PreprocessingManager。
``scripts.core.preprocessing.PreprocessingManager`` 与 ``scripts.visualization_server``
的预处理都经由同一套算子插件执行，保证行为单一真相。
"""

from .base import BasePreprocessOperator
from .registry import (
    DEFAULT_PIPELINE,
    PREPROCESS_REGISTRY,
    available_operators,
    discover_preprocessors,
    get_operator,
    register_operator,
    run_pipeline,
    resolve_pipeline,
)
from . import builtin  # noqa: F401  (触发内置算子注册)

# 自动发现 configs/preprocessors 下的自定义算子
discover_preprocessors()
