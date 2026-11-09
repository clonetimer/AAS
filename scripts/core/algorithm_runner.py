#!/usr/bin/env python
"""
算法执行引擎 - 核心模块

所有检测器（内置统计法 three_sigma/zscore/iqr 与自定义 YAML 检测器）统一通过
``detectors.loader.load_detector`` 加载与评分，CLI 与 Web 共用同一加载契约。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from detectors.loader import load_detector, extract_scores, detect_with_detector

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class AlgorithmResult:
    """算法执行结果容器

    Attributes:
        thresholds: 历史兼容字段，实际保存每个测试样本的异常分数
        detector: 使用的检测器实例（自定义检测器）或 None（内置算法）
        algorithm_type: 算法类型标识（如 "three_sigma", "mlp_detector" 等）
        train_stats: 训练集统计信息（仅内置算法使用）
        raw_scores: 原始异常分数列表
        pred_labels: 检测器给出的逐样本最终标签
        decision_thresholds: 每个样本实际使用的判定阈值（在线策略可变化）
    """
    thresholds: List[float]
    detector: Optional[Any] = None
    algorithm_type: str = ""
    train_stats: Dict[str, float] = field(default_factory=dict)
    raw_scores: List[float] = field(default_factory=list)
    score_semantics: str = "anomaly_score"
    decision_mode: str = "score_threshold"
    decision_threshold: Optional[float] = None
    pred_labels: List[int] = field(default_factory=list)
    decision_thresholds: List[float] = field(default_factory=list)

    @property
    def threshold_min(self) -> float:
        """阈值最小值"""
        return min(self.thresholds) if self.thresholds else 0.0

    @property
    def threshold_max(self) -> float:
        """阈值最大值"""
        return max(self.thresholds) if self.thresholds else 0.0

    @property
    def threshold_mean(self) -> float:
        """阈值均值"""
        return sum(self.thresholds) / len(self.thresholds) if self.thresholds else 0.0

    @property
    def sample_count(self) -> int:
        """测试样本数"""
        return len(self.thresholds)

    def summary(self) -> str:
        """生成结果摘要字符串"""
        return (
            f"AlgorithmResult(algorithm_type={self.algorithm_type!r}, "
            f"sample_count={self.sample_count}, "
            f"threshold_range=[{self.threshold_min:.4f}, {self.threshold_max:.4f}], "
            f"threshold_mean={self.threshold_mean:.4f})"
        )

    def to_dict(self, round_ndigits: int = 6) -> dict:
        """将结果转换为字典格式

        Args:
            round_ndigits: 浮点数保留的小数位数，默认 6

        Returns:
            包含算法执行结果的字典
        """
        return {
            "algorithm_type": self.algorithm_type,
            "thresholds": [round(t, round_ndigits) for t in self.thresholds],
            "threshold_min": round(self.threshold_min, round_ndigits),
            "threshold_max": round(self.threshold_max, round_ndigits),
            "threshold_mean": round(self.threshold_mean, round_ndigits),
            "sample_count": self.sample_count,
            "decision_mode": self.decision_mode,
            "train_stats": {
                k: round(v, round_ndigits) if isinstance(v, float) else v
                for k, v in self.train_stats.items()
            },
            "decision_threshold": (
                round(self.decision_threshold, round_ndigits)
                if isinstance(self.decision_threshold, float)
                else self.decision_threshold
            ),
            "pred_labels": list(self.pred_labels),
            "decision_thresholds": [round(v, round_ndigits) for v in self.decision_thresholds],
        }


# Built-in statistical detectors now live in detectors/statistical_detectors.py
# and are loaded through the shared detectors.loader (registered in
# detectors/registry.py). No separate dispatch table is needed here.


# ====================================================================
# 算法执行引擎
# ====================================================================

def _detector_detection(detector: Any, test_data: Any) -> dict[str, Any]:
    """Run detector-owned final decision while preserving the trained state."""
    return detect_with_detector(detector, test_data, preserve_state=True)


class AlgorithmRunner:
    """算法执行引擎

    所有检测器（内置统计检测器 three_sigma/zscore/iqr 与自定义 YAML 检测器）统一通过
    ``detectors.loader.load_detector`` 加载与评分，CLI 与 Web 共用同一加载契约。

    Attributes:
        detectors_dir: 检测器配置目录路径
    """

    def __init__(self, detectors_dir: Optional[str | Path] = None) -> None:
        """初始化算法执行引擎

        Args:
            detectors_dir: 检测器配置目录路径，默认为 configs/detectors
        """
        if detectors_dir is None:
            self.detectors_dir = PROJECT_ROOT / "configs" / "detectors"
        else:
            self.detectors_dir = Path(detectors_dir)

        logger.info("[AlgorithmRunner] 初始化完成，配置目录：%s", self.detectors_dir)

    def run(
        self,
        algorithm: str,
        train_values: List[float],
        test_values: List[float],
        algorithm_params: Optional[Dict[str, Any]] = None,
        train_segments: Optional[List[int]] = None,
        test_segments: Optional[List[int]] = None,
    ) -> AlgorithmResult:
        """执行算法

        内置统计检测器（three_sigma / zscore / iqr）与自定义 YAML 检测器走同一条
        加载链路：解析 detector_spec（YAML 或注册名）-> load_detector -> fit ->
        extract_scores。

        Args:
            algorithm: 算法名称（如 "three_sigma", "mlp_detector" 等）
            train_values: 训练集数值列表
            test_values: 测试集数值列表
            algorithm_params: 用户传入的算法参数，可选

        Returns:
            AlgorithmResult 算法执行结果

        Raises:
            ValueError: 当算法不存在或配置无效时
        """
        if not train_values:
            raise ValueError("train_values 不能为空")
        if not test_values:
            raise ValueError("test_values 不能为空")

        logger.info("[AlgorithmRunner] 开始执行算法：%s", algorithm)
        logger.info(
            "[AlgorithmRunner] 训练样本=%d, 测试样本=%d",
            len(train_values), len(test_values),
        )

        algorithm_params = algorithm_params or {}

        # 1. 优先匹配 YAML 配置（自定义检测器）
        config_path = self.detectors_dir / f"{algorithm}.yaml"
        if config_path.exists():
            logger.info("[AlgorithmRunner] 使用配置文件：%s", config_path)
            with open(config_path, "r", encoding="utf-8") as f:
                spec = yaml.safe_load(f)
            if not isinstance(spec, dict):
                raise ValueError(f"算法配置无效：{algorithm}，配置根节点必须是对象")
            language = str(spec.get("language") or "python").strip().lower()
            native = language in {"c", "cpp", "c++", "native"}
            if native:
                if not spec.get("library"):
                    raise ValueError(f"算法配置无效：{algorithm}，C/C++ 插件缺少 library 字段")
            elif "class_path" not in spec:
                raise ValueError(f"算法配置无效：{algorithm}，Python 插件缺少 class_path 字段")
            spec_config = spec.get("config", {}) or {}
            spec["config"] = {**spec_config, **algorithm_params}
        else:
            # 2. 否则按注册名加载（覆盖内置统计检测器）
            logger.info("[AlgorithmRunner] 按注册名加载算法：%s", algorithm)
            spec = {"name": algorithm, "config": dict(algorithm_params)}

        return self._run_detector_from_spec(algorithm, spec, train_values, test_values, train_segments=train_segments, test_segments=test_segments)

    def run_preloaded(
        self,
        algorithm: str,
        test_values: List[float],
        detector: Any,
        test_segments: Optional[List[int]] = None,
    ) -> AlgorithmResult:
        """Score test data with an already-trained detector without calling ``fit``.

        This is the model-reuse path used by the published-model evaluation API.
        Keeping it in ``AlgorithmRunner`` ensures loaded models use the same score
        normalization contract as freshly trained detectors.
        """
        if detector is None:
            raise ValueError("preloaded detector is required")
        if not test_values:
            raise ValueError("test_values 不能为空")

        import numpy as np

        test_data = np.asarray(test_values, dtype=float).reshape(len(test_values), -1)
        if callable(getattr(detector, "set_segments", None)):
            detector.set_segments(test_segments)
        try:
            detection = _detector_detection(detector, test_data)
        except Exception as e:
            logger.error("[AlgorithmRunner] 已加载模型预测失败：%s", e)
            raise

        scores = list(detection.get("anomaly_score") or [])
        train_stats = getattr(detector, "train_stats", {}) or {}
        result = AlgorithmResult(
            thresholds=scores,
            detector=detector,
            algorithm_type=algorithm,
            raw_scores=scores.copy(),
            train_stats=train_stats,
            score_semantics="anomaly_score" if scores else "label",
            decision_mode=str(detection.get("decision_mode") or "score_threshold"),
            decision_threshold=detection.get("threshold"),
            pred_labels=list(detection.get("pred_label") or []),
            decision_thresholds=list(detection.get("thresholds") or []),
        )
        logger.info("[AlgorithmRunner] 复用已训练模型：%s", result.summary())
        return result

    # ------------------------------------------------------------------
    # 统一检测器执行（内置统计检测器与自定义 YAML 检测器共用）
    # ------------------------------------------------------------------

    def _run_detector_from_spec(
        self,
        algorithm: str,
        detector_spec: Dict[str, Any],
        train_values: List[float],
        test_values: List[float],
        *,
        train_segments: Optional[List[int]] = None,
        test_segments: Optional[List[int]] = None,
    ) -> AlgorithmResult:
        """通过统一 loader 加载、训练并评分检测器

        Args:
            algorithm: 算法名称（用于结果标识与错误信息）
            detector_spec: 检测器规格（含 name / class_path / config）
            train_values: 训练集数值列表
            test_values: 测试集数值列表

        Returns:
            AlgorithmResult 算法执行结果

        Raises:
            ValueError: 当算法无法解析为已知检测器时
        """
        # Tell the shared loader where this runner resolved the YAML from.  This
        # enables runtime-rooted plugins and deterministic hot reloads.
        detector_spec = dict(detector_spec)
        detector_spec["_search_dir"] = str(self.detectors_dir)
        try:
            detector = load_detector(detector_spec)
        except (ValueError, ImportError) as e:
            raise ValueError(
                f"未知算法：{algorithm}（{type(e).__name__}: {e}）。"
                f"配置目录：{self.detectors_dir}"
            ) from e

        import numpy as np

        dimensions = len(train_values[0]) if isinstance(train_values[0], (list, tuple)) else 1
        if dimensions > 1:
            builtin_modules = {
                'detectors.statistical_detectors',
                'detectors.mlp_detector',
                'detectors.isolation_forest_detector',
                'detectors.lstm_autoencoder_detector',
            }
            if (type(detector).__module__ not in builtin_modules
                    and not getattr(detector, 'supports_multivariate', False)):
                raise ValueError('所选算法未声明支持多特征输入；请选择内置多特征算法，或使插件声明 supports_multivariate=True')
        train_data = np.asarray(train_values, dtype=float).reshape(len(train_values), -1)
        test_data = np.asarray(test_values, dtype=float).reshape(len(test_values), -1)

        if callable(getattr(detector, "set_segments", None)):
            detector.set_segments(train_segments)
        try:
            detector.fit(train_data)
        except Exception as e:
            logger.error("[AlgorithmRunner] 模型训练失败：%s", e)
            raise

        # Minimal third-party detectors may implement only fit()+score(). If
        # they inherit BaseDetector's decision rule but did not explicitly fit
        # a threshold, calibrate it from training scores here rather than from
        # the evaluation batch.
        needs_calibration = getattr(detector, "needs_threshold_calibration", None)
        if callable(needs_calibration) and needs_calibration():
            reference_scores = extract_scores(detector, train_data)
            detector.fit_threshold(reference_scores)

        if callable(getattr(detector, "set_segments", None)):
            detector.set_segments(test_segments)
        try:
            detection = _detector_detection(detector, test_data)
        except Exception as e:
            logger.error("[AlgorithmRunner] 模型预测失败：%s", e)
            raise

        scores = list(detection.get("anomaly_score") or [])
        train_stats = getattr(detector, "train_stats", {}) or {}
        result = AlgorithmResult(
            thresholds=scores,
            detector=detector,
            algorithm_type=algorithm,
            raw_scores=scores.copy(),
            train_stats=train_stats,
            score_semantics="anomaly_score" if scores else "label",
            decision_mode=str(detection.get("decision_mode") or "score_threshold"),
            decision_threshold=detection.get("threshold"),
            pred_labels=list(detection.get("pred_label") or []),
            decision_thresholds=list(detection.get("thresholds") or []),
        )

        logger.info("[AlgorithmRunner] %s", result.summary())
        return result
