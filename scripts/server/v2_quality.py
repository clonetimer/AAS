"""V2 Hampel + bounded forward fill and immutable, auditable quality flags.

All transformations operate on one channel's chronologically ordered observations.
Raw timestamps/labels are never imputed. A discontinuity or source-segment change
is a hard boundary: there is no carry-over for Hampel or forward fill.
"""
from __future__ import annotations

import math
import statistics

HAMPEL_WINDOW = 7  # centered window, 3 preceding and 3 subsequent samples
HAMPEL_N_SIGMA = 3.0
MAD_SCALE = 1.4826
MAX_FORWARD_RUN = 3


def is_valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quality_summary(qualities):
    counts = {key: 0 for key in ('observed', 'filled_missing', 'filled_outlier',
                                'unfilled_missing', 'unfilled_outlier')}
    for q in qualities:
        counts[q['status']] += 1
    total = len(qualities)
    valid = counts['observed'] + counts['filled_missing'] + counts['filled_outlier']
    return {'total': total, 'valid_samples': valid, 'invalid_samples': total - valid,
            'valid_rate': round(valid / total, 6) if total else 0,
            'outliers': counts['filled_outlier'] + counts['unfilled_outlier'],
            'source_missing': counts['filled_missing'] + counts['unfilled_missing'],
            'forward_filled': counts['filled_missing'] + counts['filled_outlier'],
            'unfilled': counts['unfilled_missing'] + counts['unfilled_outlier'],
            'by_status': counts, 'max_forward_run': MAX_FORWARD_RUN,
            'retains_quality_flags': True}


def _groups(rows):
    """Split source segments and large numeric-time discontinuities conservatively."""
    positives = [float(b['time_key'] - a['time_key']) for a, b in zip(rows, rows[1:])
                 if a['segment_id'] == b['segment_id']
                 and isinstance(a['time_key'], (int, float))
                 and isinstance(b['time_key'], (int, float))
                 and b['time_key'] > a['time_key']]
    typical = statistics.median(positives) if positives else None
    groups = []
    for index, row in enumerate(rows):
        previous = rows[index - 1] if index else None
        boundary = previous is None or row['segment_id'] != previous['segment_id']
        if not boundary and typical and isinstance(row['time_key'], (int, float)) and isinstance(previous['time_key'], (int, float)):
            boundary = row['time_key'] - previous['time_key'] > 4 * typical
        if boundary:
            groups.append([])
        groups[-1].append((index, row))
    return groups


def hampel_forward(rows, *, window=HAMPEL_WINDOW):
    """Return (row,value) pairs and quality records, one per *original* timestamp.

    Outlier detection uses the original finite values in a centered odd-size
    window; missing points are excluded from median/MAD. A detected outlier
    becomes a gap, then up to 3 consecutive gaps are forward-filled. A gap
    longer than 3 preserves NULL (including after the third filled sample).
    Leading gaps are never backward-filled. Fault labels are never changed.
    """
    if type(window) is not int or window < 3 or window > 101 or window % 2 != 1:
        raise ValueError('Hampel 窗口必须为 3～101 之间的奇数')
    half = window // 2
    output = [None] * len(rows)
    quality = [None] * len(rows)
    for group in _groups(rows):
        originals = [float(r['value_num']) if is_valid(r['value_num']) else None for _, r in group]
        outliers = set()
        for j, value in enumerate(originals):
            if value is None:
                continue
            neighborhood = [v for v in originals[max(0, j-half):j+half+1] if v is not None]
            if len(neighborhood) < 3:
                continue
            middle = statistics.median(neighborhood)
            mad = statistics.median(abs(v - middle) for v in neighborhood)
            threshold = HAMPEL_N_SIGMA * MAD_SCALE * mad
            # Flat neighborhoods: any deviation beyond machine-scale tolerance
            # is anomalous. Do not flag insignificant floating-point noise.
            epsilon = 1e-12 * max(1.0, abs(middle))
            if abs(value - middle) > max(threshold, epsilon):
                outliers.add(j)
        previous = None
        gap_run = 0
        for j, (i, row) in enumerate(group):
            value = originals[j]
            missing = value is None
            outlier = j in outliers
            if not missing and not outlier:
                transformed = value
                status = 'observed'
                previous = transformed
                gap_run = 0
            else:
                gap_run += 1
                can_fill = previous is not None and gap_run <= MAX_FORWARD_RUN
                transformed = previous if can_fill else None
                status = ('filled_' if can_fill else 'unfilled_') + ('missing' if missing else 'outlier')
            output[i] = (row, transformed)
            quality[i] = {'status': status, 'original': value, 'processed': transformed,
                          'is_outlier': outlier, 'was_missing': missing,
                          'was_filled': status.startswith('filled_'),
                          'is_valid': transformed is not None,
                          'source_segment_id': row['segment_id'],
                          'source_ordinal': row['source_ordinal']}
    return output, quality, {'hampel_window': window, 'hampel_n_sigma': HAMPEL_N_SIGMA,
                             'max_forward_run': MAX_FORWARD_RUN,
                             'retains_quality_flags': True,
                             'discontinuity_policy': 'hard_boundary'}


def preview_curve(points, qualities, *, limit=1200):
    """Bounded deterministic dual-series plot; preserve endpoints and invalid gaps."""
    n = len(points)
    indices = list(range(n)) if n <= limit else sorted({round(i*(n-1)/(limit-1)) for i in range(limit)})
    return [{'timestamp': points[i][0]['timestamp_raw'], 'source': qualities[i]['original'],
             'processed': points[i][1], 'status': qualities[i]['status']}
            for i in indices]
