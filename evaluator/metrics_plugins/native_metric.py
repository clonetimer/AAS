"""BaseMetric adapter for process-isolated AAS C/C++ plugins."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core.native_host_client import NativeHostClient
from core.native_plugins import resolve_native_library_path
from .base import BaseMetric, MetricContext


class NativeMetric(BaseMetric):
    native_plugin = True
    runtime_isolated = True

    def __init__(
        self,
        descriptor: dict[str, Any],
        *,
        project_root: str | Path | None = None,
        descriptor_path: str | Path | None = None,
    ):
        self.descriptor = dict(descriptor)
        self.name = str(descriptor.get("name") or "").strip()
        self.label = str(descriptor.get("label") or self.name)
        self.description = str(descriptor.get("description") or "")
        self.dependencies = [str(v) for v in descriptor.get("dependencies", []) or []]
        self.requires = {str(v) for v in descriptor.get("requires", []) or []}
        self.project_root = str(Path(project_root).resolve()) if project_root is not None else None
        self.descriptor_path = str(Path(descriptor_path).resolve()) if descriptor_path is not None else None
        self.library_path = str(resolve_native_library_path(
            descriptor,
            project_root=self.project_root,
            descriptor_path=self.descriptor_path,
        ))
        # Lazy start: merely opening the plugin catalog does not spawn a native
        # process. The isolated host starts on the first metric computation.
        self._host: NativeHostClient | None = None

    def _require_host(self) -> NativeHostClient:
        if self._host is None or not self._host.alive:
            old = self._host
            self._host = None
            if old is not None:
                try:
                    old.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            self._host = NativeHostClient(self.library_path, "metric")
        return self._host

    def available(self) -> bool:
        return Path(self.library_path).is_file() and super().available()

    @staticmethod
    def _pack_i32(values) -> tuple[bytes, int]:
        if values is None:
            return b"", 0
        arr = np.ascontiguousarray(np.asarray(values, dtype=np.int32).reshape(-1), dtype=np.int32)
        return arr.tobytes(order="C"), int(arr.size)

    @staticmethod
    def _pack_f64(values) -> tuple[bytes, int]:
        if values is None:
            return b"", 0
        arr = np.ascontiguousarray(np.asarray(values, dtype=np.float64).reshape(-1), dtype=np.float64)
        return arr.tobytes(order="C"), int(arr.size)

    def compute(self, ctx: MetricContext) -> float | None:
        y, yn = self._pack_i32(ctx.y_true)
        scores, sn = self._pack_f64(ctx.anomaly_score)
        preds, pn = self._pack_i32(ctx.pred_label)
        train, trn = self._pack_f64(ctx.train_values)
        test, ten = self._pack_f64(ctx.test_values)
        config_json = json.dumps(ctx.config or {}, ensure_ascii=False, default=str)
        return self._require_host().metric_compute(
            y_true=y, y_true_count=yn,
            scores=scores, scores_count=sn,
            preds=preds, preds_count=pn,
            train_values=train, train_count=trn,
            test_values=test, test_count=ten,
            config_json=config_json,
        )

    def runtime_info(self) -> dict[str, Any]:
        return self._require_host().runtime_info()

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
