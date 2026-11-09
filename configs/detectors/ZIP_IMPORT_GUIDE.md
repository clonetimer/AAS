# 检测算法 ZIP 导入

ZIP 包推荐只包含两个文件：

```text
my_detector.zip
├── my_detector.py
└── my_detector.yaml
```

检测器必须继承 `BaseDetector` 并声明唯一 `decision_mode`。最常见的是 `score_threshold`：实现 `fit()` 与 `score()`，阈值和最终标签由基类内部策略完成。

```python
from detectors.base import BaseDetector

class MyDetector(BaseDetector):
    default_decision_mode = "score_threshold"
    supported_decision_modes = ("score_threshold",)

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        pass

    def score(self, test_X):
        return [0.0 for _ in test_X]
```

YAML 示例：

```yaml
name: my_detector
label: My Detector
class_path: detectors.my_detector.MyDetector
type: machine_learning
decision_mode: score_threshold
decision_modes: [score_threshold]
dependencies: [numpy]
config:
  threshold: 0.8
config_schema:
  - name: threshold
    type: float
    min: 0
    step: 0.01
    default: 0.8
```

算法自带最终判定规则时使用 `decision_mode: native` 并实现 `native_predict()`；算法直接输出分类标签时使用 `decision_mode: label` 并实现 `label_predict()`。`native` / `label` 模式不能再叠加通用阈值策略。

导入时系统会检查 ZIP 结构、Python 语法、YAML、类路径、`BaseDetector` 契约和判定模式能力，并在成功后立即进入统一插件运行时。更新同名插件也会触发模块重新加载。
