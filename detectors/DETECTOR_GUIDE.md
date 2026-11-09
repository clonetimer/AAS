# Detector 开发规范

## 唯一判定权

每个 detector 都必须继承 `BaseDetector`，并且只允许一种最终判定路径生效。通过 `decision_mode` 明确声明：

- `score_threshold`：算法输出异常分数，检测器内部阈值策略负责最终标签；
- `native`：算法原生判定逻辑负责最终标签；
- `label`：算法直接输出最终 `0/1` 标签，分数和阈值可以不存在。

框架不会根据“是否重写 threshold()/predict()”猜测判定方式，评估器也不会对 detector 的最终标签进行第二次阈值判定。

## score_threshold

必须实现：

- `fit(train_X, train_y=None, valid_X=None, valid_y=None)`
- `score(test_X) -> list[float]`

推荐类声明：

```python
from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD

class MyDetector(BaseDetector):
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        pass

    def score(self, test_X):
        return [0.0 for _ in test_X]
```

可复用内部阈值策略：`fixed`、`quantile`、`online_quantile`、`adaptive_ewma`。训练阶段应在训练集或验证集评分上完成初始校准；Runner 会为只实现 `fit()+score()` 的最小插件补做训练期校准，避免测试集泄漏。

## native

算法自己的决策规则拥有唯一判定权。类需要声明支持 `native` 并实现 `native_predict()`：

```python
from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_NATIVE, DecisionBatch

class NativeRuleDetector(BaseDetector):
    default_decision_mode = DECISION_MODE_NATIVE
    supported_decision_modes = (DECISION_MODE_NATIVE,)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        pass

    def score(self, test_X):
        return [0.0 for _ in test_X]

    def native_predict(self, scores=None, test_X=None):
        labels = [0 for _ in test_X]
        return DecisionBatch(labels=labels, threshold=None)
```

`native` 模式禁止同时配置 `threshold_method`、`threshold_window`、`threshold_alpha` 等通用阈值参数。

## label

直接分类算法可以完全不实现 `score()`：

```python
from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_LABEL

class LabelDetector(BaseDetector):
    default_decision_mode = DECISION_MODE_LABEL
    supported_decision_modes = (DECISION_MODE_LABEL,)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        pass

    def label_predict(self, test_X):
        return [0 for _ in test_X]
```

此时 `detect()` 的 `anomaly_score` 和 `threshold` 可以为 `None`。

## Runner 执行顺序

1. `fit()`；
2. 仅 `score_threshold` 在需要时用训练/验证评分校准阈值；
3. `detect(test_X)`；
4. detector 自己根据 `decision_mode` 走唯一判定路径；
5. 输出 `decision_mode`、`pred_label`，并在存在时输出 `anomaly_score`、`threshold`、`thresholds`；
6. 评估器直接使用 `pred_label`。

## 在线阈值与状态安全

`online_quantile` / `adaptive_ewma` 执行“当前阈值先判定 -> 再更新下一时刻状态”。默认异常样本不更新阈值。

训练后的测试评估会先快照决策状态，模拟完整在线演化后再恢复训练期状态，因此测试集不会污染随后保存的模型。原生 detector 的快照同时包含 native state。

## C/C++ 原生 detector

`aas_native_v1` 保持向后兼容，并增加可选 `aas_detector_predict`：

- `score_threshold`：必须有 `aas_detector_score`；
- `native`：需要 `aas_detector_predict`，或 `aas_detector_score + aas_detector_threshold`；
- `label`：需要 `aas_detector_predict`，`aas_detector_score` 可省略。
