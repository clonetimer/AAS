from __future__ import annotations

import sys
import time
from pathlib import Path

from scripts.server.telemetry_v2_access import query_charts
from tests.test_p3e_chart_overview import chart_db

try:
    import resource
except ImportError:  # Windows has no resource module in the standard library.
    resource = None


def _peak_rss_bytes() -> int | None:
    if resource is None:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def test_50k_frame_overview_and_sample_pages_remain_bounded_and_contiguous(tmp_path: Path):
    started = time.perf_counter()
    rss_before = _peak_rss_bytes()
    db = chart_db(tmp_path, frames=50_000)

    overview = query_charts(
        db, role='train', channel_ids=[1], limit=3_000, mode='overview')['series'][0]
    first = query_charts(
        db, role='train', channel_ids=[1], limit=100, mode='page')['series'][0]
    second = query_charts(
        db, role='train', channel_ids=[1], limit=100, mode='page',
        cursor=first['next_cursor'])['series'][0]

    elapsed = time.perf_counter() - started
    rss_after = _peak_rss_bytes()
    assert overview['frame_count'] == 50_000
    assert overview['valid_sample_count'] == 49_000
    assert overview['matched_count'] == 50_001
    assert overview['duplicate_count'] == 1
    assert overview['truncated'] is True
    assert 0 < overview['plot_point_count'] <= 3_000
    assert first['page_row_count'] == second['page_row_count'] == 100
    assert [point['frame_index'] for point in first['points']] == list(range(1, 101))
    assert [point['frame_index'] for point in second['points']] == list(range(101, 201))
    assert first['points'][-1]['time_key'] < second['points'][0]['time_key']
    assert first['frame_count'] == second['frame_count'] == 50_000
    print({
        'frames': 50_000,
        'overview_points': overview['plot_point_count'],
        'page_rows': [first['page_row_count'], second['page_row_count']],
        'elapsed_seconds': round(elapsed, 3),
        'peak_rss_bytes_before': rss_before,
        'peak_rss_bytes_after': rss_after,
        'peak_rss_delta_bytes': None if rss_before is None or rss_after is None else max(0, rss_after-rss_before),
    })


def test_50k_frame_full_cursor_walk_completes_within_bounded_time(tmp_path: Path):
    """A complete 500-page walk must not rescan the full dataset per page."""
    import subprocess
    import textwrap

    db = chart_db(tmp_path, frames=50_000)
    script = textwrap.dedent('''\
        import json, sys, time
        try:
            import resource
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform != 'darwin':
                peak_rss *= 1024
        except ImportError:
            peak_rss = None
        from scripts.server.telemetry_v2_access import query_charts
        db = sys.argv[1]
        started = time.perf_counter()
        cursor = None
        frames = []
        pages = 0
        while True:
            series = query_charts(db, role='train', channel_ids=[1], mode='page',
                                  limit=100, cursor=cursor)['series'][0]
            frames.extend(point['frame_index'] for point in series['points'])
            pages += 1
            if not series['page_has_more']:
                break
            cursor = series['next_cursor']
        print(json.dumps({'pages': pages, 'frames': len(frames),
                          'first': frames[0], 'last': frames[-1],
                          'contiguous': frames == list(range(1, 50001)),
                          'elapsed': time.perf_counter() - started,
                          'peak_rss_bytes': peak_rss}))
    ''')
    completed = subprocess.run(
        [sys.executable, '-c', script, str(db)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30, check=True,
    )
    import json
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result['pages'] == 500
    assert result['frames'] == 50_000
    assert result['first'] == 1 and result['last'] == 50_000
    assert result['contiguous'] is True
    assert result['elapsed'] < 30
    print({'full_walk_pages': result['pages'], 'full_walk_frames': result['frames'],
           'full_walk_elapsed_seconds': round(result['elapsed'], 3),
           'full_walk_peak_rss_bytes': result['peak_rss_bytes']})
