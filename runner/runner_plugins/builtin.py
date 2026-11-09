"""内置执行器注册（延迟导入 LocalRunner，避免循环依赖）。"""

from __future__ import annotations

from .registry import register_runner


def register_builtin_runners() -> None:
    """注册内置执行器（LocalRunner）。在应用启动时调用一次即可。"""
    try:
        from runner.local_runner import LocalRunner

        register_runner(LocalRunner())
    except Exception:  # pylint: disable=broad-exception-caught
        # 环境异常（如依赖缺失）时不阻断，仅跳过内置 runner
        pass
