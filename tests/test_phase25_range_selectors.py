"""Phase 25: every data window picker is constrained by the actual selected source."""
from pathlib import Path
import sqlite3

import pytest

from tests.test_v2_run_subset_phase8 import labeled  # noqa: F401
from tests.test_v2_simulation_hierarchical_phase16 import source_root  # noqa: F401
from scripts.server.telemetry_v2_access import channel_time_coverage
from scripts.server.v2_simulation_injection import v2_source_catalog, v2_injection_time_bounds


def test_browser_and_preprocessing_sample_boundaries(labeled):
    result=channel_time_coverage(labeled,role='train',channel_ids=[1,2])
    assert result['has_common_window']
    assert result['min']=='2026-01-01T00:00:00.000'
    assert result['max']=='2026-01-01T00:00:05.000'
    assert len(result['coverage'])==2
    with sqlite3.connect(labeled) as db:
        db.execute('UPDATE v2_samples SET value_num=NULL WHERE segment_id=2 AND time_sort_num<2')
    result=channel_time_coverage(labeled,role='train',channel_ids=[1,2])
    assert result['min']=='2026-01-01T00:00:02.000'
    single=channel_time_coverage(labeled,role='train',channel_ids=[1])
    assert single['min']=='2026-01-01T00:00:00.000'


def test_disjoint_selected_features_and_role_isolation(labeled):
    with sqlite3.connect(labeled) as db:
        db.execute('UPDATE v2_samples SET time_sort_num=time_sort_num+100 WHERE segment_id=2')
    result=channel_time_coverage(labeled,role='train',channel_ids=[1,2])
    assert result['has_common_window'] is False
    with pytest.raises(ValueError,match='当前数据用途'):
        channel_time_coverage(labeled,role='test',channel_ids=[1])
    with pytest.raises(ValueError,match='不重复'):
        channel_time_coverage(labeled,role='train',channel_ids=[1,1])
    with pytest.raises(ValueError,match='当前数据用途'):
        channel_time_coverage(labeled,role='train',channel_ids=[9])


def test_simulation_fault_onset_limited_to_first_n_samples(source_root):
    root, _db, _ = source_root
    one=next(x for x in v2_source_catalog(root)['items'] if x['variable']=='speed')
    first=v2_injection_time_bounds(root,source_id=one['source_id'],channel_id=one['channel_id'],max_points=600)
    assert (first['start_exclusive'],first['end_inclusive'],first['sampling_rows'])==(0.,1198.,600)
    second=v2_injection_time_bounds(root,source_id=one['source_id'],channel_id=one['channel_id'],max_points=100)
    assert second['end_inclusive']==198
    with pytest.raises(ValueError):
        v2_injection_time_bounds(root,source_id=one['source_id'],channel_id=one['channel_id'],max_points=7)


def test_all_active_windows_use_shared_boundaries():
    root=Path(__file__).resolve().parents[1]
    browser=(root/'web/visualization/telemetry-v2-browser.js').read_text(encoding='utf-8')
    prep=(root/'web/visualization/telemetry-v2-preprocess.js').read_text(encoding='utf-8')
    sim=(root/'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    runner=(root/'web/visualization/runner-v2.js').read_text(encoding='utf-8')
    assert '/api/v2/telemetry/series/coverage' in browser and 'input.min=result.min' in browser
    assert '/api/v2/telemetry/series/coverage' in prep and 'x.min=result.min' in prep
    assert '/api/v2/simulation/source-time-bounds' in sim
    assert 'input.min=String(min)' in sim
    assert '/api/v2/run-dataset/coverage' in runner


def test_actual_http_time_bound_routes(source_root,monkeypatch):
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from tests.http_test_handler import AuthorizedTestHandler
    import scripts.visualization_server as main
    root, db, imported=source_root
    monkeypatch.setattr(main,'ROOT',root)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        items=v2_source_catalog(root)['items']
        channel=next(x for x in items if x['variable']=='speed')
        with urlopen(base+f"/api/v2/telemetry/series/coverage?role=train&channel_ids={channel['channel_id']}",timeout=8) as response:
            bounds=json.load(response)
        assert bounds['has_common_window'] is True
        assert bounds['min']=='2026-01-01T00:00:00.000'
        with urlopen(base+f"/api/v2/simulation/source-time-bounds?source_id={channel['source_id']}&channel_id={channel['channel_id']}&max_points=100",timeout=8) as response:
            sim=json.load(response)
        assert sim['end_inclusive']==198 and sim['sampling_rows']==100
    finally:
        server.shutdown();worker.join(timeout=5);server.server_close()
