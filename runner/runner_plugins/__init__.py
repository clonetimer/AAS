"""执行后端（Runner）插件体系（与 detectors 同构）。

- ``base.BaseRunner``：执行器插件契约（run(task_spec) -> dict）。
- ``registry``：注册表 + 按名称解析 + YAML 自动发现。
- ``builtin``：内置 LocalRunner（本地子进程执行）。

新增一种执行后端（如远程 / 容器 / 集群）只需写一个子类 + 一个 YAML 描述，
并在应用启动时调用 ``register_builtin_runners()``（container 已调用）。
"""

from .base import BaseRunner
from .registry import (
    RUNNER_REGISTRY,
    available_runners,
    discover_runners,
    get_runner,
    register_runner,
    run_task,
)
from . import builtin  # noqa: F401  (定义 register_builtin_runners，不在导入期执行以避免循环依赖)

# 自动发现 configs/runners 下的自定义执行器
discover_runners()
