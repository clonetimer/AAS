# 检测器判定权与阈值架构

## 核心原则：一个检测器只有一个最终判定权

阈值不是 AAS 的一级插件能力。每个检测算法必须显式声明 `decision_mode`，框架不会再通过“是否重写了 threshold()/predict()”猜测语义。

支持三种模式：

- `score_threshold`：算法产生异常分数，由检测器内部阈值策略产生最终标签；
- `native`：算法自己的原生判定逻辑拥有最终判定权，通用阈值策略禁止参与；
- `label`：算法直接输出最终标签，异常分数和阈值都可以不存在。

评估器只消费检测器给出的 `pred_label`，绝不再次执行 `score >= threshold` 来重建标签。

## score_threshold 模式

`BaseDetector` 提供四种检测器内部策略：

- `fixed`：固定阈值；
- `quantile`：训练/验证评分分位数，校准后保持不变；
- `online_quantile`：滑动窗口在线分位数；
- `adaptive_ewma`：EWMA 均值/方差自适应阈值。

推荐 YAML：

```yaml
decision_mode: score_threshold
decision_modes: [score_threshold]
config:
  threshold_method: online_quantile
  threshold_window: 200
  threshold_update_on_anomaly: false
```

在线策略严格执行“当前阈值先判定 -> 记录逐样本阈值 -> 再更新下一时刻状态”，默认异常样本不参与更新，降低阈值污染风险。

## native 模式

算法原生逻辑是唯一判定源。此模式下 `threshold_method`、`threshold_window`、`threshold_alpha` 等通用阈值配置会被运行时拒绝。

Isolation Forest 支持两种互斥方式：

```yaml
# AAS 内部阈值负责最终判定
decision_mode: score_threshold
```

此时 sklearn 使用 `contamination=auto`，只提供 `score_samples()`，不会使用 sklearn 的 offset/predict 作为最终判定。

```yaml
# sklearn 原生判定负责最终结果
decision_mode: native
```

此时直接采用 sklearn `predict()`，`contamination` 控制 sklearn 原生 offset，AAS 通用阈值策略不初始化也不校准。

C/C++ 原生检测器在 `native` 模式下可以导出 `aas_detector_predict`；也可以导出 `aas_detector_score + aas_detector_threshold`，由原生算法提供唯一阈值。

## label 模式

直接分类型算法可以只输出标签：

```python
class MyClassifier(BaseDetector):
    default_decision_mode = "label"
    supported_decision_modes = ("label",)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        ...

    def label_predict(self, test_X):
        return [0, 1, 0, ...]
```

这类算法不需要 `score()`，统一结果中的 `anomaly_score` 和 `threshold` 可以为 `None`。C/C++ `label` 模式使用可选 ABI `aas_detector_predict`。

## 训练、评估和状态安全

`score_threshold` 模式在训练/验证数据上完成初始阈值校准，避免测试集泄漏。评估阶段对在线/自适应策略真实模拟逐样本更新，但评估前会快照检测器决策状态，评估结束后恢复训练期状态，因此测试集更新不会进入随后保存的模型。

原生检测器的评估快照还包含原生模型状态，以防原生 `predict` 自身是有状态的。

统一检测结果至少包含：

```text
decision_mode
pred_label
```

有分数时增加 `anomaly_score`；有可审计阈值时增加 `threshold / thresholds`。指标计算始终以 `pred_label` 为最终分类结果。

## 兼容性

旧配置没有 `decision_mode` 时，内置及普通第三方分数型检测器默认按 `score_threshold` 处理。旧 `threshold_plugin` 仍只作为四种内置阈值策略名称的配置别名读取，但不存在独立“阈值插件”注册、安装或运行链路。


### 框架最终入口不可重写

框架拥有 `threshold()/predict()/score_and_predict()/detect()` 这组最终调度入口；检测器插件不得重写它们。插件只按 `decision_mode` 实现 `score()`、`native_predict()` 或 `label_predict()`，从代码层阻止第二套判定逻辑绕开唯一判定权。
