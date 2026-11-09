"""
MLP 神经网络异常检测器
用于时间序列数据的异常检测
"""
import numpy as np
from typing import Tuple

from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD


class MLPDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    """
    基于 MLP 的异常检测器

    使用自编码器结构，通过重构误差来检测异常
    """

    default_threshold_method = "quantile"

    def __init__(self, config: dict):
        """
        初始化 MLP 检测器

        Args:
            config: 配置字典，包含以下参数：
                - input_size: 输入特征维度（可选，默认自动推断）
                - hidden_sizes: 隐藏层大小列表，默认 [64, 32, 16]
                - learning_rate: 学习率，默认 0.001
                - epochs: 训练轮数，默认 100
                - batch_size: 批次大小，默认 32
                - threshold_percentile: 阈值百分位数，默认 95.0
                - verbose: 是否打印训练信息，默认 True
        """
        super().__init__(config)

        self.input_size = config.get("input_size", None)
        self.hidden_sizes = config.get("hidden_sizes", [64, 32, 16])
        self.learning_rate = config.get("learning_rate", 0.001)
        self.epochs = config.get("epochs", 100)
        self.batch_size = config.get("batch_size", 32)
        self.threshold_value = config.get("threshold", None)
        self.contamination = config.get("contamination", 0.05)
        self.verbose = config.get("verbose", False)
        # Local RNG: no process-wide state changes during concurrent training.
        self.random_state = int(config.get("random_state") if config.get("random_state") is not None else 42)

        # 模型参数
        self.weights = []
        self.biases = []
        self.mean_ = None
        self.std_ = None
        self.is_trained = False

    def _sigmoid(self, x: np.ndarray) -> np.ndarray:
        """Sigmoid 激活函数"""
        return 1 / (1 + np.exp(-np.clip(x, -500, 500)))

    def _sigmoid_derivative(self, x: np.ndarray) -> np.ndarray:
        """Sigmoid 导数"""
        s = self._sigmoid(x)
        return s * (1 - s)

    def _relu(self, x: np.ndarray) -> np.ndarray:
        """ReLU 激活函数"""
        return np.maximum(0, x)

    def _relu_derivative(self, x: np.ndarray) -> np.ndarray:
        """ReLU 导数"""
        return (x > 0).astype(float)

    def _initialize_weights(self, input_size):
        """初始化网络权重"""
        rng = np.random.default_rng(self.random_state)

        # 构建网络结构：input -> hidden -> ... -> hidden -> output
        layer_sizes = [input_size] + self.hidden_sizes + [input_size]

        self.weights = []
        self.biases = []

        for i in range(len(layer_sizes) - 1):
            w = rng.standard_normal((layer_sizes[i], layer_sizes[i+1])) * np.sqrt(2.0 / layer_sizes[i])
            b = np.zeros((1, layer_sizes[i+1]))
            self.weights.append(w)
            self.biases.append(b)

    def _forward(self, X: np.ndarray) -> Tuple[np.ndarray, list]:
        """
        前向传播

        Returns:
            output: 输出
            activations: 各层的激活值（用于反向传播）
        """
        activations = [X]
        current = X

        for i in range(len(self.weights)):
            z = np.dot(current, self.weights[i]) + self.biases[i]

            if i == len(self.weights) - 1:
                current = z
            else:
                current = self._relu(z)

            activations.append(current)

        return current, activations

    def _backward(self, X: np.ndarray, y: np.ndarray, activations: list):
        """反向传播，更新权重"""
        m = X.shape[0]

        delta = activations[-1] - y

        for i in range(len(self.weights) - 1, -1, -1):
            dw = np.dot(activations[i].T, delta) / m
            db = np.sum(delta, axis=0, keepdims=True) / m

            self.weights[i] -= self.learning_rate * dw
            self.biases[i] -= self.learning_rate * db

            if i > 0:
                delta = np.dot(delta, self.weights[i].T) * self._relu_derivative(activations[i])

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        """
        训练模型

        Args:
            train_X: 训练数据
            train_y: 训练标签（可选）
            valid_X: 验证数据（可选）
            valid_y: 验证标签（可选）
        """
        data = np.array(train_X)

        if len(data.shape) == 3:
            data = data[:, -1, :]

        self.mean_ = np.mean(data, axis=0)
        self.std_ = np.std(data, axis=0) + 1e-8
        X_normalized = (data - self.mean_) / self.std_

        if self.input_size is None:
            self.input_size = X_normalized.shape[1]

        self._initialize_weights(self.input_size)

        if self.verbose:
            print("开始训练 MLP 自编码器...")
            print(f"  - 样本数：{X_normalized.shape[0]}")
            print(f"  - 特征数：{X_normalized.shape[1]}")
            print(f"  - 网络结构：{[self.input_size] + self.hidden_sizes + [self.input_size]}")

        for epoch in range(self.epochs):
            output, activations = self._forward(X_normalized)
            self._backward(X_normalized, X_normalized, activations)

            if self.verbose and (epoch + 1) % 20 == 0:
                loss = np.mean((output - X_normalized) ** 2)
                print(f"  Epoch {epoch+1}/{self.epochs}, Loss: {loss:.6f}")

        reference_X = valid_X if valid_X is not None else train_X
        self.is_trained = True
        reference_scores = self.score(reference_X)

        extra = {"contamination": self.contamination, "threshold_quantile_method": "linear"}
        if self.threshold_value is not None:
            extra["threshold"] = self.threshold_value
            default_method = "fixed"
        else:
            default_method = "quantile"
        self.threshold_value = self.fit_threshold(
            reference_scores,
            default_method=default_method,
            config=extra,
        )

        if self.verbose:
            print(f"训练完成！阈值：{self.threshold_value:.6f}")

    def _compute_reconstruction_error(self, X: np.ndarray) -> np.ndarray:
        """计算重构误差"""
        output, _ = self._forward(X)
        errors = np.mean((output - X) ** 2, axis=1)
        return errors

    def score(self, test_X) -> list[float]:
        """
        获取每个样本的异常分数

        Args:
            test_X: 测试数据

        Returns:
            scores: 异常分数列表
        """
        if not self.is_trained:
            raise RuntimeError("模型未训练，请先调用 fit() 方法")

        data = np.array(test_X)

        if len(data.shape) == 3:
            data = data[:, -1, :]

        X_normalized = (data - self.mean_) / self.std_
        errors = self._compute_reconstruction_error(X_normalized)

        return [float(e) for e in errors]

    def get_meta(self) -> dict:
        """获取检测器元信息"""
        meta = super().get_meta()
        meta["name"] = "mlp_detector"
        return meta
