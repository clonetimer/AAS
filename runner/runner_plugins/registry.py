"""执行器注册表、按名称解析与 YAML 自动发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from core.plugin_loader import import_plugin_class

from .base import BaseRunner


RUNNER_REGISTRY: dict[str, BaseRunner] = {}


def register_runner(runner: BaseRunner) -> None:
    if not isinstance(runner, BaseRunner):
        raise TypeError("Runner plugin must inherit BaseRunner.")
    if not runner.name:
        raise ValueError("Runner must define a non-empty `name`.")
    RUNNER_REGISTRY[runner.name] = runner


def get_runner(name: str) -> Optional[BaseRunner]:
    runner = RUNNER_REGISTRY.get(name)
    if runner is None and name == "local":
        # 延迟注册内置 LocalRunner，避免导入期循环依赖
        try:
            from . import builtin

            builtin.register_builtin_runners()
            runner = RUNNER_REGISTRY.get(name)
        except Exception:  # pylint: disable=broad-exception-caught
            runner = None
    return runner


def available_runners() -> list[dict]:
    return [
        {
            "name": name,
            "label": r.label,
            "description": r.description,
            "dependencies": r.dependencies,
        }
        for name, r in RUNNER_REGISTRY.items()
    ]


def run_task(name: str, task_spec: dict) -> dict:
    runner = get_runner(name)
    if runner is None:
        raise ValueError(
            f"Unsupported runner: {name!r}. Available: {sorted(RUNNER_REGISTRY)}"
        )
    return runner.run(task_spec)


def discover_runners(search_dir: Optional[Path] = None) -> int:
    """从 YAML 描述文件自动发现并注册自定义执行器。

    YAML 形如::

        name: remote
        label: 远程执行器
        description: 通过 SSH 提交任务
        class_path: my_package.my_module.RemoteRunner
        dependencies: [paramiko]

    返回成功注册的执行器数量。
    """
    if search_dir is None:
        search_dir = Path(__file__).parent.parent.parent / "configs" / "runners"
    if not search_dir.exists():
        return 0
    count = 0
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if not isinstance(data, dict) or "class_path" not in data:
            continue
        try:
            cls = import_plugin_class(data["class_path"], search_dir)
            runner = cls()
            if data.get("name"):
                runner.name = data["name"]
            if data.get("label"):
                runner.label = data["label"]
            if data.get("description"):
                runner.description = data["description"]
            if data.get("dependencies"):
                runner.dependencies = data["dependencies"]
            register_runner(runner)
            count += 1
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return count
