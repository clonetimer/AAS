from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Sequence

from detectors.decision import (
    DECISION_MODE_LABEL,
    DECISION_MODE_NATIVE,
    DECISION_MODE_SCORE_THRESHOLD,
    DecisionBatch,
    normalize_decision_mode,
    normalize_decision_output,
)
from detectors.thresholding import build_threshold_strategy, normalize_threshold_method


class BaseDetector(ABC):
    """Unified detector contract with exactly one final decision authority.

    A detector declares ``decision_mode`` instead of letting the framework infer
    semantics from whether a method happened to be overridden.

    - ``score_threshold``: score -> detector-owned threshold strategy -> label.
    - ``native``: algorithm-owned ``native_predict`` decides the final label.
    - ``label``: algorithm-owned ``label_predict`` directly returns final labels.

    Threshold strategies are internal detector components, not top-level AAS
    plugins.  They are only legal in ``score_threshold`` mode.
    """

    default_threshold_method = "fixed"
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    _FRAMEWORK_OWNED_DECISION_METHODS = (
        "threshold",
        "predict",
        "score_and_predict",
        "detect",
    )

    _GENERIC_THRESHOLD_CONFIG_KEYS = {
        "threshold_method",
        "threshold_mode",
        "threshold_plugin",  # legacy alias
        "threshold_window",
        "threshold_alpha",
        "threshold_sigma",
        "threshold_update_on_anomaly",
        "threshold_quantile_method",
    }

    def __init__(self, config: dict):
        # These methods form the framework's single-authority decision pipeline.
        # A detector may customize only score(), native_predict() or
        # label_predict(); overriding a final dispatcher would make
        # decision_mode advisory instead of enforceable.
        for method_name in self._FRAMEWORK_OWNED_DECISION_METHODS:
            if getattr(type(self), method_name) is not getattr(BaseDetector, method_name):
                raise TypeError(
                    f"{type(self).__name__} must not override framework-owned "
                    f"{method_name}(); implement score(), native_predict(), or "
                    "label_predict() according to decision_mode instead"
                )

        self.config = dict(config or {})
        self.decision_mode = normalize_decision_mode(
            self.config.get("decision_mode"), self.default_decision_mode
        )
        supported = tuple(
            normalize_decision_mode(mode) for mode in self.supported_decision_modes
        )
        if self.decision_mode not in supported:
            raise ValueError(
                f"{self.__class__.__name__} does not support decision_mode={self.decision_mode!r}; "
                f"supported={list(supported)}"
            )

        if self.decision_mode != DECISION_MODE_SCORE_THRESHOLD:
            conflicts = sorted(
                key for key in self._GENERIC_THRESHOLD_CONFIG_KEYS if key in self.config
            )
            if conflicts:
                raise ValueError(
                    f"decision_mode={self.decision_mode!r} cannot use generic threshold "
                    f"configuration: {', '.join(conflicts)}. The algorithm has sole "
                    "decision authority in native/label mode."
                )

        self._last_scores: list[float] | None = None
        self._last_labels: list[int] | None = None
        self._last_thresholds: list[float] = []
        self._threshold: float | None = None
        self._threshold_strategy = None
        self._threshold_strategy_method: str | None = None

    @abstractmethod
    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        raise NotImplementedError

    def score(self, test_X) -> list[float]:
        """Return anomaly scores when this decision mode exposes them.

        ``label`` detectors are allowed to omit score generation entirely.
        Score-based modes must override this method.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not expose anomaly scores"
        )

    def _merged_threshold_config(self, config: dict | None = None) -> dict:
        merged = dict(self.config)
        if config:
            merged.update(config)
        return merged

    def _require_score_threshold_mode(self, operation: str) -> None:
        if self.decision_mode != DECISION_MODE_SCORE_THRESHOLD:
            raise RuntimeError(
                f"{operation} is only available for decision_mode='score_threshold'; "
                f"current mode is {self.decision_mode!r}"
            )

    def _ensure_threshold_strategy(
        self,
        *,
        default_method: str | None = None,
        config: dict | None = None,
    ):
        self._require_score_threshold_mode("generic threshold strategy")
        merged = self._merged_threshold_config(config)
        fallback = default_method or self.default_threshold_method
        method = normalize_threshold_method(merged, fallback)
        if self._threshold_strategy is None or self._threshold_strategy_method != method:
            self._threshold_strategy = build_threshold_strategy(merged, default_method=fallback)
            self._threshold_strategy_method = method
        else:
            self._threshold_strategy.config.update(merged)
        return self._threshold_strategy

    def fit_threshold(
        self,
        scores: Sequence[float],
        *,
        default_method: str | None = None,
        config: dict | None = None,
    ) -> float:
        """Calibrate the detector-owned generic threshold from reference scores."""
        self._require_score_threshold_mode("fit_threshold")
        strategy = self._ensure_threshold_strategy(default_method=default_method, config=config)
        value = float(strategy.calibrate(scores))
        self._threshold = value
        return value

    def needs_threshold_calibration(self) -> bool:
        """Whether a runner should calibrate an uninitialized generic strategy."""
        if self.decision_mode != DECISION_MODE_SCORE_THRESHOLD:
            return False
        strategy = self._ensure_threshold_strategy()
        return strategy.current_threshold is None

    def threshold(self, scores=None, test_X=None) -> float:
        """Return the current generic threshold for ``score_threshold`` mode."""
        del test_X
        self._require_score_threshold_mode("threshold")
        strategy = self._ensure_threshold_strategy()
        if strategy.current_threshold is None:
            strategy.calibrate([] if scores is None else scores)
        self._threshold = float(strategy.current_threshold)
        return self._threshold

    def native_predict(self, scores=None, test_X=None) -> DecisionBatch | tuple | dict:
        """Algorithm-owned final decision hook for ``decision_mode='native'``."""
        raise NotImplementedError(
            f"{self.__class__.__name__} declares native decision mode but does not "
            "implement native_predict()"
        )

    def label_predict(self, test_X) -> DecisionBatch | Sequence[int] | dict:
        """Direct-label hook for ``decision_mode='label'``."""
        raise NotImplementedError(
            f"{self.__class__.__name__} declares label decision mode but does not "
            "implement label_predict()"
        )

    def _score_threshold_predict(self, scores: Sequence[float]) -> DecisionBatch:
        values = [float(v) for v in scores]
        strategy = self._ensure_threshold_strategy()
        if strategy.current_threshold is None:
            # Compatibility fallback for minimal score-only detectors. Normal
            # runners calibrate on train/validation data before evaluation.
            strategy.calibrate(values)
        labels, thresholds = strategy.predict(values)
        threshold = float(strategy.current_threshold)
        return DecisionBatch(labels=list(labels), threshold=threshold, thresholds=list(thresholds))

    def predict(self, scores=None, test_X=None) -> tuple[list[int], float | None]:
        """Return final labels using exactly one decision path."""
        values = None if scores is None else [float(v) for v in scores]
        expected_length = len(values) if values is not None else None
        if expected_length is None and test_X is not None:
            try:
                expected_length = len(test_X)
            except TypeError:
                expected_length = None
        if expected_length is None:
            raise ValueError("cannot determine sample count for detector prediction")

        if self.decision_mode == DECISION_MODE_SCORE_THRESHOLD:
            if values is None:
                raise ValueError("score_threshold mode requires anomaly scores")
            batch = self._score_threshold_predict(values).validate(expected_length)
        elif self.decision_mode == DECISION_MODE_NATIVE:
            batch = normalize_decision_output(
                self.native_predict(values, test_X=test_X), expected_length
            )
        elif self.decision_mode == DECISION_MODE_LABEL:
            if test_X is None:
                raise ValueError("label mode requires test_X")
            batch = normalize_decision_output(self.label_predict(test_X), expected_length)
        else:  # pragma: no cover - guarded by normalize_decision_mode
            raise RuntimeError(f"unsupported decision mode: {self.decision_mode}")

        self._last_scores = None if values is None else list(values)
        self._last_labels = list(batch.labels)
        self._last_thresholds = list(batch.thresholds)
        self._threshold = batch.threshold
        return list(batch.labels), batch.threshold

    def get_threshold(self) -> float | None:
        if self._threshold is not None:
            return float(self._threshold)
        if self.decision_mode != DECISION_MODE_SCORE_THRESHOLD:
            return None
        try:
            return self.threshold(self._last_scores or [])
        except (TypeError, ValueError, RuntimeError, OverflowError):
            value = self.config.get("threshold")
            return float(value) if value is not None else None

    def get_threshold_trace(self) -> list[float]:
        return list(self._last_thresholds)

    def export_threshold_state(self) -> dict | None:
        if self.decision_mode != DECISION_MODE_SCORE_THRESHOLD or self._threshold_strategy is None:
            return None
        return self._threshold_strategy.state_dict()

    def restore_threshold_state(self, state: dict | None) -> None:
        if not state:
            return
        self._require_score_threshold_mode("restore_threshold_state")
        method = str(state.get("name") or self.default_threshold_method)
        strategy = self._ensure_threshold_strategy(
            default_method=method,
            config={"threshold_method": method},
        )
        strategy.load_state_dict(state)
        self._threshold = strategy.current_threshold

    def export_evaluation_state(self) -> dict:
        """Snapshot mutable decision state so evaluation cannot poison a model."""
        return {
            "threshold_state": deepcopy(self.export_threshold_state()),
            "last_scores": deepcopy(self._last_scores),
            "last_labels": deepcopy(self._last_labels),
            "last_thresholds": deepcopy(self._last_thresholds),
            "threshold": self._threshold,
        }

    def restore_evaluation_state(self, state: dict | None) -> None:
        if not state:
            return
        threshold_state = state.get("threshold_state")
        if threshold_state is not None:
            self.restore_threshold_state(threshold_state)
        self._last_scores = deepcopy(state.get("last_scores"))
        self._last_labels = deepcopy(state.get("last_labels"))
        self._last_thresholds = list(state.get("last_thresholds") or [])
        self._threshold = state.get("threshold")

    def exposes_scores(self) -> bool:
        """Whether this detector can expose anomaly scores for the current mode."""
        return type(self).score is not BaseDetector.score

    def _optional_scores(self, test_X) -> list[float] | None:
        has_score = self.exposes_scores()
        if self.decision_mode == DECISION_MODE_SCORE_THRESHOLD and not has_score:
            raise RuntimeError(
                f"{self.__class__.__name__} uses score_threshold mode but does not implement score()"
            )
        if has_score:
            return [float(v) for v in self.score(test_X)]
        return None

    def score_and_predict(self, test_X) -> tuple[list[float] | None, list[int], float | None]:
        # Deliberately call the BaseDetector dispatcher instead of a possibly
        # overridden public predict() method.  The public decision pipeline is
        # framework-owned; plugins customize only score/native_predict/label_predict.
        scores = self._optional_scores(test_X)
        labels, threshold = BaseDetector.predict(self, scores, test_X=test_X)
        return scores, labels, threshold

    def detect(self, test_X) -> dict[str, Any]:
        """Return the canonical complete result owned by this detector.

        Evaluation/report code must consume ``pred_label`` rather than rebuild
        labels from scores and a threshold. This is essential for native,
        label-only, and online/adaptive decisions.
        """
        # Keep the canonical pipeline non-overridable from inside BaseDetector.
        scores, labels, threshold = BaseDetector.score_and_predict(self, test_X)
        return {
            "anomaly_score": None if scores is None else list(scores),
            "pred_label": list(labels),
            "is_anomaly": [bool(v) for v in labels],
            "threshold": threshold,
            "thresholds": self.get_threshold_trace(),
            "decision_mode": self.decision_mode,
        }

    def save(self, model_path: str) -> None:
        raise NotImplementedError("This detector does not implement model persistence.")

    def load(self, model_path: str) -> None:
        raise NotImplementedError("This detector does not implement model persistence.")

    def get_meta(self) -> dict:
        return {
            "name": self.__class__.__name__,
            "config": self.config,
            "decision_mode": self.decision_mode,
            "threshold_method": (
                self._threshold_strategy_method
                if self.decision_mode == DECISION_MODE_SCORE_THRESHOLD
                else None
            ),
        }
