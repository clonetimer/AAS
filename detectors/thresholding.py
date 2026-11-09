"""Internal threshold strategies owned by detector instances.

Threshold handling is deliberately *not* a top-level AAS plugin family.  A
registered detector must be able to turn its anomaly scores into final anomaly
labels by itself.  Built-in detectors may reuse the strategies in this module,
while algorithm-native or direct-label detectors select a different explicit
``decision_mode`` and never enter this module.

The public detector configuration is intentionally flat so it can be rendered by
existing detector parameter UIs:

``threshold_method``
    ``fixed`` | ``quantile`` | ``online_quantile`` | ``adaptive_ewma``.
``threshold_mode``
    Backward-compatible convenience alias. ``online`` selects
    ``online_quantile`` and ``adaptive`` selects ``adaptive_ewma`` when no
    explicit method is provided.
``threshold_window`` / ``window``
    Window size for ``online_quantile``.
``threshold_alpha`` / ``alpha`` and ``threshold_sigma`` / ``sigma``
    EWMA parameters.
``threshold_update_on_anomaly``
    Whether a sample already judged anomalous may update the online state.
    Defaults to False to reduce threshold poisoning.

Legacy ``threshold_plugin`` is accepted only as a configuration alias for the
four built-in method names.  There is no registry, discovery or installation
surface for threshold code anymore.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Iterable, Sequence


SUPPORTED_THRESHOLD_METHODS = (
    "fixed",
    "quantile",
    "online_quantile",
    "adaptive_ewma",
)


def _finite_scores(scores: Iterable[float]) -> list[float]:
    values = [float(v) for v in scores]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("threshold scores must be finite")
    return values


def quantile_threshold(
    scores: Sequence[float],
    *,
    contamination: float = 0.05,
    ratio: float | None = None,
    method: str = "floor",
) -> float:
    """Return an upper-tail threshold without depending on NumPy.

    ``floor`` preserves the historical IF/LSTM/evaluator behavior. ``linear``
    preserves NumPy-percentile style interpolation used by the legacy MLP.
    """
    values = sorted(_finite_scores(scores))
    if not values:
        raise ValueError("scores is empty")

    if ratio is None:
        contamination = min(max(float(contamination), 0.0), 1.0)
        ratio = 1.0 - contamination
    ratio = min(max(float(ratio), 0.0), 1.0)

    if method == "linear":
        pos = ratio * (len(values) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return float(values[lo])
        frac = pos - lo
        return float(values[lo] * (1.0 - frac) + values[hi] * frac)

    idx = int(ratio * (len(values) - 1))
    return float(values[max(0, min(idx, len(values) - 1))])


def normalize_threshold_method(config: dict | None, default_method: str = "fixed") -> str:
    cfg = config or {}
    explicit = cfg.get("threshold_method") or cfg.get("threshold_strategy")
    legacy = cfg.get("threshold_plugin")
    method = str(explicit or legacy or "").strip().lower()
    mode = str(cfg.get("threshold_mode") or "").strip().lower()

    if not method:
        if mode == "online":
            method = "online_quantile"
        elif mode == "adaptive":
            method = "adaptive_ewma"
        else:
            method = str(default_method or "fixed").strip().lower()

    aliases = {
        "offline": str(default_method or "fixed").strip().lower(),
        "online": "online_quantile",
        "adaptive": "adaptive_ewma",
        "ewma": "adaptive_ewma",
        "moving_quantile": "online_quantile",
    }
    method = aliases.get(method, method)
    if method not in SUPPORTED_THRESHOLD_METHODS:
        raise ValueError(
            f"不支持的阈值策略: {method}；可选值: {', '.join(SUPPORTED_THRESHOLD_METHODS)}"
        )
    return method


class ThresholdStrategy:
    """Stateful threshold policy embedded in one detector instance."""

    name = "base"
    online = False

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.current_threshold: float | None = None

    def calibrate(self, scores: Sequence[float]) -> float:
        raise NotImplementedError

    def _should_update(self, is_anomaly: bool) -> bool:
        return (not is_anomaly) or bool(
            self.config.get("threshold_update_on_anomaly", self.config.get("update_on_anomaly", False))
        )

    def update_one(self, score: float, is_anomaly: bool) -> None:
        del score, is_anomaly
        return None

    def state_dict(self) -> dict:
        return {
            "name": self.name,
            "config": dict(self.config),
            "current_threshold": self.current_threshold,
        }

    def load_state_dict(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise TypeError("threshold state must be a dict")
        saved_name = str(state.get("name") or self.name)
        if saved_name != self.name:
            raise ValueError(f"threshold state mismatch: {saved_name} != {self.name}")
        saved_config = state.get("config")
        if isinstance(saved_config, dict):
            self.config.update(saved_config)
        value = state.get("current_threshold")
        self.current_threshold = None if value is None else float(value)

    def predict(self, scores: Sequence[float]) -> tuple[list[int], list[float]]:
        values = _finite_scores(scores)
        if self.current_threshold is None:
            self.calibrate(values)
        if self.current_threshold is None:
            raise RuntimeError("threshold strategy did not produce a threshold")

        labels: list[int] = []
        thresholds: list[float] = []
        for score in values:
            threshold = float(self.current_threshold)
            is_anomaly = score >= threshold
            labels.append(1 if is_anomaly else 0)
            thresholds.append(threshold)
            if self.online and self._should_update(is_anomaly):
                self.update_one(score, is_anomaly)
        return labels, thresholds


class FixedThresholdStrategy(ThresholdStrategy):
    name = "fixed"

    def calibrate(self, scores: Sequence[float]) -> float:
        value = self.config.get("threshold", self.config.get("decision_threshold", 0.5))
        threshold = float(value)
        if not math.isfinite(threshold):
            raise ValueError("fixed threshold must be finite")
        self.current_threshold = threshold
        return threshold


class QuantileThresholdStrategy(ThresholdStrategy):
    name = "quantile"

    def calibrate(self, scores: Sequence[float]) -> float:
        method = str(
            self.config.get("threshold_quantile_method", self.config.get("quantile_method", "floor"))
        ).strip().lower()
        self.current_threshold = quantile_threshold(
            scores,
            contamination=float(self.config.get("contamination", 0.05)),
            ratio=self.config.get("threshold_ratio", self.config.get("ratio")),
            method=method,
        )
        return float(self.current_threshold)


class OnlineQuantileThresholdStrategy(ThresholdStrategy):
    name = "online_quantile"
    online = True

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        size = int(self.config.get("threshold_window", self.config.get("window", 200)))
        self.window = deque(maxlen=max(2, size))

    def _recompute(self) -> float:
        self.current_threshold = quantile_threshold(
            list(self.window),
            contamination=float(self.config.get("contamination", 0.05)),
            ratio=self.config.get("threshold_ratio", self.config.get("ratio")),
            method=str(
                self.config.get("threshold_quantile_method", self.config.get("quantile_method", "floor"))
            ).strip().lower(),
        )
        return float(self.current_threshold)

    def calibrate(self, scores: Sequence[float]) -> float:
        values = _finite_scores(scores)
        if not values:
            # Explicit fixed seed is useful for stream-only detectors.
            seed = self.config.get("threshold")
            if seed is None:
                raise ValueError("online_quantile requires calibration scores or threshold seed")
            self.current_threshold = float(seed)
            return float(self.current_threshold)
        self.window.clear()
        self.window.extend(values[-self.window.maxlen :])
        return self._recompute()

    def update_one(self, score: float, is_anomaly: bool) -> None:
        self.window.append(float(score))
        self._recompute()

    def state_dict(self) -> dict:
        state = super().state_dict()
        state["window"] = list(self.window)
        return state

    def load_state_dict(self, state: dict) -> None:
        super().load_state_dict(state)
        size = int(self.config.get("threshold_window", self.config.get("window", 200)))
        self.window = deque((float(v) for v in state.get("window", [])), maxlen=max(2, size))


class AdaptiveEWMAThresholdStrategy(ThresholdStrategy):
    name = "adaptive_ewma"
    online = True

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.mean: float | None = None
        self.variance: float = 0.0

    @property
    def alpha(self) -> float:
        raw = float(self.config.get("threshold_alpha", self.config.get("alpha", 0.2)))
        return min(max(raw, 1e-6), 1.0)

    @property
    def sigma(self) -> float:
        return max(0.0, float(self.config.get("threshold_sigma", self.config.get("sigma", 3.0))))

    def _refresh_threshold(self) -> float:
        if self.mean is None:
            seed = self.config.get("threshold", 0.5)
            self.current_threshold = float(seed)
        else:
            std = math.sqrt(max(self.variance, 0.0))
            if self.sigma > 0:
                std = max(std, float(self.config.get("threshold_min_std", 1e-12)))
            self.current_threshold = float(self.mean + self.sigma * std)
        return float(self.current_threshold)

    def calibrate(self, scores: Sequence[float]) -> float:
        values = _finite_scores(scores)
        if not values:
            return self._refresh_threshold()
        self.mean = sum(values) / len(values)
        self.variance = sum((v - self.mean) ** 2 for v in values) / len(values)
        return self._refresh_threshold()

    def update_one(self, score: float, is_anomaly: bool) -> None:
        value = float(score)
        if self.mean is None:
            self.mean = value
            self.variance = 0.0
            self._refresh_threshold()
            return
        alpha = self.alpha
        old_mean = self.mean
        new_mean = (1.0 - alpha) * old_mean + alpha * value
        # EWMA variance update around the moving mean.  This retains constant
        # memory and reacts smoothly to distribution drift.
        delta = value - old_mean
        self.variance = (1.0 - alpha) * (self.variance + alpha * delta * delta)
        self.mean = new_mean
        self._refresh_threshold()

    def state_dict(self) -> dict:
        state = super().state_dict()
        state.update({"mean": self.mean, "variance": self.variance})
        return state

    def load_state_dict(self, state: dict) -> None:
        super().load_state_dict(state)
        mean = state.get("mean")
        self.mean = None if mean is None else float(mean)
        self.variance = float(state.get("variance", 0.0))


def build_threshold_strategy(
    config: dict | None,
    *,
    default_method: str = "fixed",
) -> ThresholdStrategy:
    cfg = dict(config or {})
    method = normalize_threshold_method(cfg, default_method)
    classes = {
        "fixed": FixedThresholdStrategy,
        "quantile": QuantileThresholdStrategy,
        "online_quantile": OnlineQuantileThresholdStrategy,
        "adaptive_ewma": AdaptiveEWMAThresholdStrategy,
    }
    return classes[method](cfg)
