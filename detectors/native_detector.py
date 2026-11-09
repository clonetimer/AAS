"""BaseDetector adapter for process-isolated AAS C/C++ plugins."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core.native_host_client import NativeHostClient
from core.native_plugins import (
    _flatten_float,
    _project_root_from_descriptor_path,
    is_native_descriptor,
    normalize_native_language,
    resolve_native_library_path,
)
from detectors.base import BaseDetector
from detectors.decision import (
    DECISION_MODE_LABEL,
    DECISION_MODE_NATIVE,
    DECISION_MODE_SCORE_THRESHOLD,
    DecisionBatch,
)


class NativeDetector(BaseDetector):
    """Detector backed by a DLL/SO executed in a separate native host process.

    Native plugins must explicitly select their decision authority with YAML or
    config ``decision_mode``. For backward compatibility an undeclared native
    detector defaults to ``score_threshold``.
    """

    native_plugin = True
    runtime_isolated = True
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (
        DECISION_MODE_SCORE_THRESHOLD,
        DECISION_MODE_NATIVE,
        DECISION_MODE_LABEL,
    )

    def __init__(
        self,
        config: dict | None = None,
        *,
        descriptor: dict[str, Any],
        project_root: str | Path | None = None,
        descriptor_path: str | Path | None = None,
    ):
        effective_config = dict(config or {})
        if "decision_mode" not in effective_config and descriptor.get("decision_mode") is not None:
            effective_config["decision_mode"] = descriptor.get("decision_mode")
        super().__init__(effective_config)
        self.descriptor = dict(descriptor)
        self.project_root = str(Path(project_root).resolve()) if project_root is not None else None
        self.descriptor_path = str(Path(descriptor_path).resolve()) if descriptor_path is not None else None
        self.library_path = str(resolve_native_library_path(
            self.descriptor,
            project_root=self.project_root,
            descriptor_path=self.descriptor_path,
        ))
        self._host: NativeHostClient | None = None
        self._native_capabilities: dict[str, Any] = {}
        # Keep the last known-good state in Python. If the native host crashes,
        # the replacement host restores this state without loading the DLL/SO
        # into the long-running AAS service process.
        self._last_native_state: bytes = b""
        self._load_native(restore_state=False)
        self._validate_native_decision_contract()

    def _config_json(self) -> str:
        return json.dumps(self.config or {}, ensure_ascii=False, default=str)

    def _load_native(self, *, restore_state: bool = True) -> None:
        old = self._host
        self._host = None
        if old is not None:
            try:
                old.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        host = NativeHostClient(self.library_path, "detector", config_json=self._config_json())
        self._host = host
        try:
            self._native_capabilities = dict(host.runtime_info())
        except Exception:  # pylint: disable=broad-exception-caught
            self._native_capabilities = {}
        if restore_state and self._last_native_state:
            host.detector_import_state(self._last_native_state)

    def _require_host(self) -> NativeHostClient:
        if self._host is None or not self._host.alive:
            self._load_native(restore_state=True)
        assert self._host is not None
        return self._host

    def _has_native(self, capability: str) -> bool:
        key = f"detector_has_{capability}"
        if key not in self._native_capabilities:
            try:
                self._native_capabilities = dict(self._require_host().runtime_info())
            except Exception:  # pylint: disable=broad-exception-caught
                return False
        return bool(self._native_capabilities.get(key))

    def _validate_native_decision_contract(self) -> None:
        if self.decision_mode == DECISION_MODE_SCORE_THRESHOLD and not self._has_native("score"):
            raise ValueError(
                "Native detector decision_mode='score_threshold' requires "
                "aas_detector_score"
            )
        if self.decision_mode == DECISION_MODE_NATIVE:
            if not self._has_native("predict") and not (
                self._has_native("score") and self._has_native("threshold")
            ):
                raise ValueError(
                    "Native detector decision_mode='native' requires either "
                    "aas_detector_predict, or both aas_detector_score and "
                    "aas_detector_threshold"
                )
        if self.decision_mode == DECISION_MODE_LABEL and not self._has_native("predict"):
            raise ValueError(
                "Native detector decision_mode='label' requires aas_detector_predict"
            )

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        values = _flatten_float(train_X)
        host = self._require_host()
        host.detector_fit(values.tobytes(order="C"), int(values.size))
        self._last_native_state = host.detector_export_state()

    def exposes_scores(self) -> bool:
        return self._has_native("score")

    def score(self, test_X) -> list[float]:
        values = _flatten_float(test_X)
        available, payload = self._require_host().detector_score_optional(
            values.tobytes(order="C"), int(values.size)
        )
        if not available:
            raise RuntimeError(
                "原生 detector 未导出 aas_detector_score；label 模式可以不提供评分，"
                "其他模式需要评分"
            )
        expected = int(values.size) * np.dtype(np.float64).itemsize
        if len(payload) != expected:
            raise RuntimeError(
                f"原生 detector 返回评分长度异常：期望 {expected} 字节，实际 {len(payload)} 字节"
            )
        out = np.frombuffer(payload, dtype=np.float64, count=int(values.size))
        return [float(v) for v in out.tolist()]

    def _native_labels(self, test_X) -> list[int]:
        values = _flatten_float(test_X)
        available, payload = self._require_host().detector_predict(
            values.tobytes(order="C"), int(values.size)
        )
        if not available:
            raise RuntimeError("原生 detector 未导出 aas_detector_predict")
        expected = int(values.size) * np.dtype(np.int32).itemsize
        if len(payload) != expected:
            raise RuntimeError(
                f"原生 detector 返回标签长度异常：期望 {expected} 字节，实际 {len(payload)} 字节"
            )
        raw = np.frombuffer(payload, dtype=np.int32, count=int(values.size))
        return [1 if int(v) else 0 for v in raw.tolist()]

    def _native_threshold(self, scores) -> float | None:
        if scores is None or not self._has_native("threshold"):
            return None
        score_values = _flatten_float(scores)
        available, value = self._require_host().detector_threshold(
            score_values.tobytes(order="C"), int(score_values.size)
        )
        return float(value) if available and value is not None else None

    def native_predict(self, scores=None, test_X=None) -> DecisionBatch:
        if test_X is None:
            raise ValueError("native decision mode requires test_X")
        if self._has_native("predict"):
            labels = self._native_labels(test_X)
            threshold = self._native_threshold(scores)
            trace = [threshold for _ in labels] if threshold is not None else []
            return DecisionBatch(labels=labels, threshold=threshold, thresholds=trace)

        if scores is None:
            raise RuntimeError("native threshold decision requires anomaly scores")
        threshold = self._native_threshold(scores)
        if threshold is None:
            raise RuntimeError(
                "decision_mode='native' has neither aas_detector_predict nor "
                "aas_detector_threshold"
            )
        labels = [1 if float(score) >= threshold else 0 for score in scores]
        return DecisionBatch(
            labels=labels,
            threshold=threshold,
            thresholds=[threshold for _ in labels],
        )

    def label_predict(self, test_X) -> DecisionBatch:
        labels = self._native_labels(test_X)
        return DecisionBatch(labels=labels, threshold=None, thresholds=[])

    def _export_native_state(self) -> bytes:
        if self._host is not None and self._host.alive:
            self._last_native_state = self._host.detector_export_state()
        elif not self._last_native_state:
            self._last_native_state = self._require_host().detector_export_state()
        return bytes(self._last_native_state)

    def _import_native_state(self, payload: bytes) -> None:
        self._last_native_state = bytes(payload or b"")
        host = self._require_host()
        if self._last_native_state:
            host.detector_import_state(self._last_native_state)

    def export_evaluation_state(self) -> dict:
        state = super().export_evaluation_state()
        state["native_state"] = self._export_native_state()
        return state

    def restore_evaluation_state(self, state: dict | None) -> None:
        if not state:
            return
        native_state = bytes(state.get("native_state") or b"")
        if native_state:
            self._import_native_state(native_state)
        super().restore_evaluation_state(state)

    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state["_persisted_native_state"] = self._export_native_state()
        state["_host"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        payload = bytes(state.pop("_persisted_native_state", state.get("_last_native_state", b"")) or b"")
        self.__dict__.update(state)
        self._host = None
        self._last_native_state = payload
        self._load_native(restore_state=bool(payload))
        self._validate_native_decision_contract()

    def save(self, model_path: str) -> None:
        Path(model_path).write_bytes(self._export_native_state())

    def load(self, model_path: str) -> None:
        self._import_native_state(Path(model_path).read_bytes())

    def get_meta(self) -> dict[str, Any]:
        meta = super().get_meta()
        meta.update({
            "language": normalize_native_language(self.descriptor.get("language")),
            "abi": self.descriptor.get("abi"),
            "library": self.descriptor.get("library"),
            "native": True,
            "runtime_isolation": "separate_process",
            "native_capabilities": {
                "score": self._has_native("score"),
                "predict": self._has_native("predict"),
                "threshold": self._has_native("threshold"),
            },
            "native_host_pid": self._host.pid if self._host is not None and self._host.alive else None,
        })
        return meta

    def close(self) -> None:
        host = self._host
        self._host = None
        if host is not None:
            try:
                host.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def instantiate_native_detector(detector_spec: dict[str, Any], search_dir: str | Path | None = None) -> NativeDetector:
    """Create a process-isolated native detector from a descriptor."""
    from detectors.registry import get_detector_descriptor

    descriptor = dict(detector_spec)
    name = str(descriptor.get("name") or "").strip()
    if not is_native_descriptor(descriptor) and name:
        discovered = get_detector_descriptor(name, search_dir)
        if is_native_descriptor(discovered):
            merged = dict(discovered)
            merged.update({k: v for k, v in descriptor.items() if k not in {"config"}})
            merged["config"] = dict(descriptor.get("config") or discovered.get("config") or {})
            descriptor = merged
    if not is_native_descriptor(descriptor):
        raise ValueError("不是 C/C++ 原生 detector 描述")

    config = dict(descriptor.get("config") or {})
    if "decision_mode" not in config and descriptor.get("decision_mode") is not None:
        config["decision_mode"] = descriptor.get("decision_mode")
    project_root: Path | None = None
    descriptor_path = descriptor.get("_descriptor_path")
    if search_dir is not None:
        directory = Path(search_dir).resolve()
        if directory.name == "detectors" and directory.parent.name == "configs":
            project_root = directory.parent.parent
    if project_root is None and descriptor_path:
        project_root = _project_root_from_descriptor_path(Path(descriptor_path))
    return NativeDetector(
        config,
        descriptor=descriptor,
        project_root=project_root,
        descriptor_path=descriptor_path,
    )
