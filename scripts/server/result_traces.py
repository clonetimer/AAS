"""Bounded, truthful score-vs-decision-threshold history for report cards."""
from __future__ import annotations

import math
from typing import Any

MAX_SAVED_POINTS = 1600


def numeric(value: Any) -> float | None:
    """Return a JSON-safe finite real without coercing missing values to zero."""
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def chart_trace(result: dict[str, Any], limit: int = MAX_SAVED_POINTS) -> dict[str, Any] | None:
    """Store actual score/threshold pairs, never synthesize a decision boundary.

    Data retained at source timestamps; plotted previews are bounded so an
    accidental multi-million-row HTTP result cannot inflate summary.json.
    """
    if not isinstance(result, dict):
        return None
    rows = result.get('rows')
    raw_scores = result.get('threshold')  # legacy response name: anomaly scores
    if not isinstance(rows, list) or not isinstance(raw_scores, list) or not rows:
        return None
    if len(rows) != len(raw_scores):
        return None
    n = len(rows)
    scores = [numeric(s) for s in raw_scores]
    if not any(s is not None for s in scores):
        return None
    dynamic = result.get('decision_thresholds')
    if isinstance(dynamic, list) and len(dynamic) == n:
        limits = [numeric(v) for v in dynamic]
    else:
        limits = None
    scalar = numeric(result.get('decision_threshold'))
    pred = result.get('pred_labels')
    if not isinstance(pred, list) or len(pred) != n or any(
        value not in (0, 1, '0', '1') for value in pred
    ):
        # A detector's decisions are authoritative. Never invent alerts with
        # bool(value): the string '0' would otherwise become an alert (1).
        pred = None
    # Uniform-time coverage + extrema for spike retention. Points are exact
    # observations, not interpolated or averaged fictitious measurements.
    indices = set()
    buckets = max(1, min(n, limit // 4))
    if n <= limit:
        indices = set(range(n))
    else:
        indices.update((0, n - 1))
        for bucket in range(buckets):
            lo = bucket * n // buckets
            hi = min(n, (bucket + 1) * n // buckets)
            indices.add(lo)
            for sequence in (scores, limits):
                if sequence is None:
                    continue
                available = [i for i in range(lo, hi) if sequence[i] is not None]
                if available:
                    indices.add(min(available, key=lambda i, values=sequence: values[i]))
                    indices.add(max(available, key=lambda i, values=sequence: values[i]))
    sampled = sorted(indices)
    if len(sampled) > limit:
        sampled = [sampled[i * (len(sampled) - 1) // (limit - 1)] for i in range(limit)]
    trace_rows = []
    for idx in sampled:
        row = rows[idx] if isinstance(rows[idx], dict) else {}
        entry = {'timestamp': str(row.get('timestamp', idx + 1))[:90]}
        frame_index=row.get('frame_index')
        if type(frame_index) is int and frame_index>=1:
            entry['frame_index']=frame_index
        label = row.get('label')
        if label in (0, 1, '0', '1'):
            entry['label'] = int(label)
        trace_rows.append(entry)
    return {
        'format_version': 1,
        'rows': trace_rows,
        'scores': [scores[i] for i in sampled],
        'decision_threshold': scalar,
        'decision_thresholds': [limits[i] for i in sampled] if limits is not None else None,
        'pred_labels': [int(pred[i]) for i in sampled] if pred is not None else None,
        'total_samples': n,
        'sampled': len(sampled) != n,
        'algorithm': str(result.get('algorithm') or '')[:100],
    }
