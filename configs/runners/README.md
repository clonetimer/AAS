# 自定义执行后端（插件）

把一种新的执行后端做成插件（如远程 / 容器 / 集群调度），只需两步，**无需改动 orchestrator 与 LocalRunner**：

1. 编写执行器类（继承 `runner.runner_plugins.base.BaseRunner`，保持 `run(task_spec) -> dict` 接口）：

```python
from runner.runner_plugins.base import BaseRunner

class RemoteRunner(BaseRunner):
    name = "remote"                # 执行器键名
    label = "远程执行器"
    dependencies = ["paramiko"]

    def run(self, task_spec: dict) -> dict:
        # 通过 SSH / 消息队列提交任务并取回结果，返回与 LocalRunner 同构的结果 dict
        ...
```

2. 在 `configs/runners/` 下放一个 YAML 描述（文件名任意，扩展名 `.yaml`）：

```yaml
name: remote
label: 远程执行器
description: 通过 SSH 提交任务
class_path: my_package.my_module.RemoteRunner
dependencies: [paramiko]
```

放置后该执行器自动注册；在实验配置 `runtime.runner: remote` 即可启用。

> 内置 `LocalRunner` 已注册为 `local`，由 `core/container.py` 在启动时调用
> `register_builtin_runners()` 完成注册。这复用了与检测器（detector）完全相同的插件范式。
