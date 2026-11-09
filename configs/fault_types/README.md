# 故障类型插件

故障类型插件用于扩展「模拟数据库」中的故障注入类型。当前支持 Python 插件。

YAML 示例：

```yaml
name: my_fault
label: 我的故障
class_path: src.fault_plugins.my_fault.MyFaultType
description: 在目标区间按固定比例降低数值
formula: y' = y · (1 - loss_ratio)
uses_severity: false
supports_time_range: true
dependencies: []
config_schema:
  - name: loss_ratio
    label: 损失比例
    type: float
    default: 0.2
    min: 0
    max: 1
    step: 0.05
```

Python 类必须继承 `src.fault_plugins.base.BaseFaultType` 并实现：

```python
def inject(self, data, config):
    ...
    return data
```

可使用 `self.apply_to_range(data, config, fn)` 复用系统统一的「全表 / 指定时间段」选择逻辑。
