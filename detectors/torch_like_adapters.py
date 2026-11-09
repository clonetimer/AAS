"""Five anomaly detectors adapted from clonetimer/torch_like main.

Upstream revision: 1e603915896e33fd9f326073c42a477718166b68 (Apache-2.0).
Modified for the AAS BaseDetector interface, sample-level output, deterministic
checkpointing and explicit non-leaking threshold calibration.

Input: 2-D (observations, channels) or 3-D (independent windows, time, channels).
A 2-D input is ONE continuous segment for CUSUM/EWMA. When multiple simulation
runs are concatenated, callers MUST score those segments independently to avoid
cross-run state; otherwise use independent 3-D windows.
"""
from __future__ import annotations

import math
import numpy as np

from detectors.base import BaseDetector


def _observations(values):
    x = np.asarray(values, dtype=np.float64)
    if x.ndim not in (2, 3) or any(d == 0 for d in x.shape):
        raise ValueError("expected nonempty 2D [N,C] or 3D [N,L,C] signal")
    if not np.isfinite(x).all():
        raise ValueError("nonfinite telemetry must be handled before anomaly detection")
    return x


class _BaselineStat(BaseDetector):
    """Reference-only channel statistics, no test labels or inference refit."""
    default_threshold_method = "fixed"
    supports_multivariate = True

    def set_segments(self, lengths):
        if lengths is not None and (not lengths or any(type(n) is not int or n<1 for n in lengths)):
            raise ValueError("segment lengths must be positive integers")
        self._segment_lengths = list(lengths) if lengths is not None else None

    def _sequences(self, z):
        if z.ndim == 3:
            return z, False, None
        lens = getattr(self, "_segment_lengths", None) or [len(z)]
        if sum(lens) != len(z):
            raise ValueError("segment lengths do not match scored data")
        slices=[];start=0
        for count in lens:
            slices.append(z[start:start+count][None,...]);start+=count
        return slices, True, lens


    def __init__(self, config):
        super().__init__(config)
        self.min_std = float(config.get("min_std", 1e-8))
        if not math.isfinite(self.min_std) or self.min_std <= 0:
            raise ValueError("min_std must be finite > 0")
        self.reference_mean = None
        self.reference_std = None
        self.train_stats = {}

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        x = _observations(train_X)
        flat = x.reshape(-1, x.shape[-1])
        if len(flat) < 2:
            raise ValueError("baseline requires >=2 healthy observations")
        self.reference_mean = flat.mean(axis=0)
        self.reference_std = np.maximum(flat.std(axis=0), self.min_std)
        self.train_stats = {"mean": self.reference_mean.tolist(), "std": self.reference_std.tolist()}
        self.fit_threshold([], default_method="fixed", config={"threshold": self._control_limit()})

    def _control_limit(self):
        raise NotImplementedError

    def _standardize(self, x):
        if self.reference_mean is None:
            raise RuntimeError("not fitted")
        if x.shape[-1] != len(self.reference_mean):
            raise ValueError("feature dimension/order differs from training")
        return (x - self.reference_mean) / self.reference_std


class TorchLikeCUSUMDetector(_BaselineStat):
    """Two-sided tabular CUSUM, independently reset on each 3D window."""

    def __init__(self, config):
        super().__init__(config)
        self.k = float(config.get("k", 0.5))
        self.h = float(config.get("h", 5.0))
        if not math.isfinite(self.k) or self.k < 0 or not math.isfinite(self.h) or self.h <= 0:
            raise ValueError("CUSUM needs finite k>=0 and h>0")

    def _control_limit(self):
        return self.h

    def score(self, test_X):
        z = self._standardize(_observations(test_X))
        windows, sequential, _ = self._sequences(z)
        if not sequential:
            chunks = [windows[i:i+1] for i in range(len(windows))]
        else:
            chunks = windows
        scores=[]
        for block in chunks:
            positive=np.zeros(block.shape[-1]);negative=np.zeros(block.shape[-1])
            for i in range(block.shape[1]):
                zt=block[0,i]
                positive=np.maximum(0,positive+zt-self.k)
                negative=np.maximum(0,negative-zt-self.k)
                value=float(np.maximum(positive,negative).max())
                if sequential:scores.append(value)
            if not sequential:scores.append(value)
        return scores


class TorchLikeEWMADetector(_BaselineStat):
    """Finite-horizon standardized EWMA score, reset at each 3D window."""

    def __init__(self, config):
        super().__init__(config)
        self.alpha = float(config.get("alpha", 0.2))
        self.control_limit = float(config.get("control_limit", 3.0))
        if (not math.isfinite(self.alpha) or not 0 < self.alpha <= 1 or
                not math.isfinite(self.control_limit) or self.control_limit <= 0):
            raise ValueError("EWMA requires 0 < alpha <= 1, control_limit > 0")

    def _control_limit(self):
        return self.control_limit

    def score(self, test_X):
        z = self._standardize(_observations(test_X))
        windows, sequential, _ = self._sequences(z)
        chunks=windows if sequential else [windows[i:i+1] for i in range(len(windows))]
        scores=[]
        for block in chunks:
            state=np.zeros(block.shape[-1])
            for i in range(block.shape[1]):
                state=self.alpha*block[0,i]+(1-self.alpha)*state
                variance=self.alpha/(2-self.alpha)*(1-(1-self.alpha)**(2*(i+1)))
                value=float(np.max(np.abs(state)/math.sqrt(variance)))
                if sequential:scores.append(value)
            if not sequential:scores.append(value)
        return scores


class TorchLikePCASPEDetector(BaseDetector):
    """PCA squared prediction error (Q statistic) on the residual subspace."""
    default_threshold_method = "quantile"
    supports_multivariate = True

    def __init__(self, config):
        super().__init__(config)
        self.n_components = int(config.get("n_components", 1))
        self.min_std = float(config.get("min_std", 1e-8))
        self.contamination = float(config.get("contamination", 0.05))
        if self.n_components < 1 or self.min_std <= 0 or not 0 < self.contamination < 0.5:
            raise ValueError("PCA: invalid n_components/min_std/contamination")
        self.mean_ = self.std_ = self.components_ = None
        self.train_stats = {}

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        x = _observations(train_X)
        flat = x.reshape(-1, x.shape[-1])
        n, c = flat.shape
        if c < 2 or self.n_components >= c or n <= c:
            raise ValueError("PCA-SPE needs >=2 channels, n_components < channels and enough healthy points")
        self.mean_ = flat.mean(axis=0)
        self.std_ = np.maximum(flat.std(axis=0, ddof=1), self.min_std)
        centered = (flat-self.mean_)/self.std_
        covariance = centered.T @ centered / (n-1)
        eigvals, eigvecs = np.linalg.eigh(covariance)
        idx = np.argsort(eigvals)[::-1][:self.n_components]
        if np.any(eigvals[idx] <= 1e-10):
            raise ValueError("PCA-SPE has zero-variance principal components")
        self.components_ = eigvecs[:,idx]
        self.train_stats = {"n_components":self.n_components, "explained_eigenvalues":eigvals[idx].tolist()}
        reference = valid_X if valid_X is not None and len(valid_X) > 1 else train_X
        values = self.score(reference)
        extra={"contamination": self.contamination}
        if "threshold" in self.config:
            extra["threshold"] = float(self.config["threshold"])
            default="fixed"
        else:
            default="quantile"
        self.fit_threshold(values, default_method=default, config=extra)

    def score(self, test_X):
        if self.components_ is None:
            raise RuntimeError("not fitted")
        x = _observations(test_X)
        if x.shape[-1] != len(self.mean_):
            raise ValueError("PCA-SPE feature count differs from trained model")
        centered = (x-self.mean_)/self.std_
        projection = centered @ self.components_
        residual = centered - projection @ self.components_.T
        # 3-D input = one scored window; 2-D input = one score per timestamp.
        scores = np.square(residual).sum(axis=-1)
        return (scores[:, -1] if x.ndim == 3 else scores).tolist()
