# 检测算法插件

检测算法与指标、数据源、预处理、报告、Runner、训练策略使用同一套插件运行时。新增算法无需修改核心注册表；放入一个 YAML 描述文件和一个 Python 类即可即时发现和加载。

## 最小插件

代码放在 `detectors/my_detector.py`：

```python
from detectors.base import BaseDetector

class MyDetector(BaseDetector):
    def __init__(self, config: dict):
        super().__init__(config)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        pass

    def score(self, test_X) -> list[float]:
        # 必须一条样本对应一个连续异常分数，分数越大越异常
        return [0.0 for _ in test_X]
```

配置放在 `configs/detectors/my_detector.yaml`：

```yaml
name: my_detector
label: My Detector
description: 自定义异常检测算法
class_path: detectors.my_detector.MyDetector
type: machine_learning
dependencies: [numpy]
config:
  threshold: 0.8
  scale: 1.0
config_schema:
  - name: threshold
    label: 判定阈值
    type: float
    min: 0
    step: 0.01
    default: 0.8
  - name: scale
    label: 缩放系数
    type: float
    step: 0.1
    default: 1.0
```

## 接口契约

必须继承 `detectors.base.BaseDetector`，并显式遵守一种 `decision_mode`：

- `score_threshold`：实现 `fit()` 与 `score()`，最终标签由检测器内部阈值策略产生；
- `native`：实现 `fit()` 与 `native_predict()`，算法原生规则拥有唯一最终判定权；
- `label`：实现 `fit()` 与 `label_predict()`，允许完全没有 `score()` 和阈值。

`detect()` 是统一结果入口。评估器直接使用其 `pred_label`，不会再根据 score/threshold 做第二次判定。阈值不再是一级插件。

基类内置四种检测器内部阈值策略，可作为算法参数选择：

- `fixed`：固定阈值；
- `quantile`：训练/验证评分分位数，训练完成后固定；
- `online_quantile`：滑动窗口在线分位数；
- `adaptive_ewma`：EWMA 均值/方差自适应阈值。

常用参数：`threshold_method`、`threshold_window`、`threshold_alpha`、`threshold_sigma`、`threshold_update_on_anomaly`。在线策略按“先用旧阈值判定当前样本，再更新下一时刻阈值”的顺序执行；默认异常样本不参与更新。若算法具有专属判定规则，应显式声明 `decision_mode: native` 并实现 `native_predict()`；直接分类算法声明 `decision_mode: label` 并实现 `label_predict()`。三种判定模式互斥。

## YAML 字段

- `name`：插件唯一名称；
- `class_path`：Python 类路径；
- `label` / `description`：展示信息；
- `type`：建议使用 `statistical`、`machine_learning`、`deep_learning`，其他自定义类型也支持；
- `dependencies`：Python 模块名列表，用于可用性检测和依赖安装；
- `decision_mode` / `decision_modes`：默认判定模式与插件允许的判定模式；
- `config`：默认运行参数；
- `config_schema`：参数 UI 约束。建议显式提供；未提供时只推断数据类型，不会给未知数值参数强加范围。

## 加载与更新

插件管理页和算法导入接口都会在保存后立即发现插件。更新已有 Python 文件后，加载器会清理本地模块缓存并重新导入，不要求为了代码更新重启服务。

如果依赖缺失，插件仍会显示在能力清单中并标记缺失依赖；安装依赖后可重新加载。

## 模型持久化

`type: statistical` 默认只保存配置和阈值信息；`machine_learning` 默认保存 pickle；`deep_learning` 使用现有深度学习保存策略；其他自定义类型默认 pickle，避免训练好的第三方模型被静默丢弃。

### 在线阈值与评估状态

训练完成后的测试/评估会按样本顺序真实模拟 `online_quantile` / `adaptive_ewma` 的阈值演化，并记录逐样本 `decision_thresholds` 与 `pred_labels`。评估结束后会恢复训练期阈值状态，再保存模型，因此测试集不会污染已发布模型的在线阈值状态。真正的长驻在线检测实例则可以持续保留并更新该状态。


### 框架最终入口不可重写

框架拥有 `threshold()/predict()/score_and_predict()/detect()` 这组最终调度入口；检测器插件不得重写它们。插件只按 `decision_mode` 实现 `score()`、`native_predict()` 或 `label_predict()`，从代码层阻止第二套判定逻辑绕开唯一判定权。
