"""Phase 17 regression: calendar picker and labeled-run view for V2 preprocessing."""
from pathlib import Path
import sqlite3

import pytest

from tests.test_v2_run_subset_phase8 import labeled
from scripts.server.telemetry_v2_access import list_channels
from scripts.server.telemetry_v2_preprocess import preprocess


def _request(channel_id, role, *, start=None, end=None, method='moving_average'):
    return dict(channel_id=channel_id, role=role, method=method,
                params={'window': 2} if method == 'moving_average' else {},
                start=start, end=end)


def test_preprocess_uses_same_verified_train_view_as_browser(labeled):
    assert {1, 2, 3, 4} == {c['id'] for c in list_channels(labeled, role='train')['items']}
    request = _request(1, 'train', start='2026-01-01 00:00:01', end='2026-01-01 00:00:03')
    before = sqlite3.connect(labeled).execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]
    preview = preprocess(labeled, request)
    assert (preview['input_count'], preview['output_count']) == (3, 3)
    assert (preview['first_timestamp'], preview['last_timestamp']) == ('1', '3')
    assert sqlite3.connect(labeled).execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0] == before
    written = preprocess(labeled, {**request, 'confirm': True}, commit=True, actor='phase17')
    assert written['output_count'] == 3
    with sqlite3.connect(labeled) as conn:
        assert conn.execute('SELECT role FROM v2_segments WHERE id=?',
                            (written['output_segment_id'],)).fetchone()[0] == 'train'
        assert conn.execute('SELECT role FROM v2_segments WHERE id=1').fetchone()[0] == 'simulation'


def test_preprocess_test_view_includes_heldout_fault_without_training_leakage(labeled):
    assert {5, 6, 7, 8} == {c['id'] for c in list_channels(labeled, role='test')['items']}
    result = preprocess(labeled, _request(7, 'test', start='2026-01-01 00:00:04',
                                          end='2026-01-01 00:00:05'))
    assert result['output_count'] == 2
    with pytest.raises(ValueError, match='只允许从训练'):
        preprocess(labeled, _request(7, 'test', method='zscore'))
    with pytest.raises(ValueError, match='没有有效数据分片'):
        preprocess(labeled, _request(9, 'train'))  # original train split but FAULT run
    with pytest.raises(ValueError, match='没有有效数据分片'):
        preprocess(labeled, _request(1, 'test'))


def test_preprocess_calendar_interval_validation_and_empty_result(labeled):
    with pytest.raises(ValueError, match='开始时间晚于结束时间'):
        preprocess(labeled, _request(1, 'train', start='2026-01-01 00:00:04',
                                        end='2026-01-01 00:00:03'))
    with pytest.raises(ValueError, match='当前时间窗没有采样'):
        preprocess(labeled, _request(1, 'train', start='2026-01-03 00:00:00'))


def test_preprocess_form_calendar_controls_are_native_and_optional():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'web/visualization/preprocess.html').read_text(encoding='utf-8')
    js = (root / 'web/visualization/telemetry-v2-preprocess.js').read_text(encoding='utf-8')
    assert 'id="v2PrepStart" type="datetime-local" step="1"' in html
    assert 'id="v2PrepEnd" type="datetime-local" step="1"' in html
    assert "const start=timeForApi('v2PrepStart'),end=timeForApi('v2PrepEnd')" in js
    assert '开始时间不能晚于结束时间' in js
