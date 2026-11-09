"""Phase24: UI calendar bounds match selected actual V2 telemetry samples."""
from pathlib import Path
import sqlite3

import pytest

from tests.test_v2_run_subset_phase8 import labeled  # noqa: F401 - real V2 fixture
from scripts.server.v2_run_dataset import selection_coverage

SPEED = 'adcs.rw.speed_rad_s'
CURRENT = 'adcs.rw.current_a'


def coverage(db, runs, features=(SPEED, CURRENT), role='train'):
    return selection_coverage(db, role=role, source_ids=runs, feature_keys=list(features))


def test_common_intersection_and_run_role(labeled):
    got=coverage(labeled, [1,2])
    assert got['has_common_window'] is True
    assert got['start_s']==0 and got['end_s']==5
    assert len(got['coverage'])==4
    assert {r['source_id'] for r in got['coverage']} == {1,2}
    test=coverage(labeled,[3,4],role='test')
    assert test['has_common_window'] is True
    assert len(test['coverage'])==4
    with pytest.raises(ValueError,match='划分'):
        coverage(labeled,[3],role='train')


def test_actual_non_null_coverage_intersection(labeled):
    with sqlite3.connect(labeled) as db:
        # Note that min/max must consider real non-null samples, not simply
        # source_row_count or the declared segment bounds.
        db.execute('UPDATE v2_samples SET value_num=NULL WHERE segment_id=2 AND time_sort_num<2')
        db.execute('UPDATE v2_samples SET value_num=NULL WHERE segment_id=3 AND time_sort_num>4')
    got=coverage(labeled,[1,2]);assert got['start_s']==2 and got['end_s']==4


def test_disjoint_time_intervals_are_detected(labeled):
    with sqlite3.connect(labeled) as db:
        db.execute('UPDATE v2_samples SET time_sort_num=time_sort_num+100 WHERE segment_id=2')
    got=coverage(labeled,[1],[SPEED,CURRENT])
    assert got['has_common_window'] is False
    assert got['start_s']==100 and got['end_s']==5


def test_missing_selected_channel_rejected(labeled):
    with sqlite3.connect(labeled) as db:
        db.execute('DELETE FROM v2_samples WHERE segment_id=2')
    with pytest.raises(ValueError,match='没有有效采样'):
        coverage(labeled,[1])
    with pytest.raises(ValueError,match='缺少可用参量'):
        coverage(labeled,[1],['not.a.feature'])


def test_calendar_frontend_contract():
    root=Path(__file__).resolve().parents[1]
    js=(root/'web/visualization/runner-v2.js').read_text(encoding='utf-8')
    html=(root/'web/visualization/runner.html').read_text(encoding='utf-8')
    assert '/api/v2/run-dataset/coverage' in js
    for key in ('trainStart','trainEnd','evalStart','evalEnd'):
        assert f'id="{key}"' in html
    assert 'validateCoverage' in js
    assert 'has_common_window' in js
    assert 'input.min=min;input.max=max' in js
    assert 'trainTimeCoverage' in html and 'evalTimeCoverage' in html
