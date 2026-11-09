"""Regression checks for reusable selection and explicit alignment contracts."""
from pathlib import Path
import sqlite3

import pytest

from scripts.core.multi_table_fusion import fuse_sources
from scripts.core.training import TrainingPipeline

ROOT = Path(__file__).resolve().parents[1]


def make_offset_db(path):
    with sqlite3.connect(path) as conn:
        for table, offset in (('anchor', 0), ('sensor', 0.4)):
            conn.execute(f'CREATE TABLE "{table}" (timestamp TEXT, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)')
            conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?, ?, ?)', [
                (f'2026-06-01 00:00:{i:02d}' + ('.400' if offset else ''),
                 'M', 'P', 'sensor', table, float(i) + (20.0 if offset else 1.0), 0)
                for i in range(12)
            ])


def test_causal_alignment_avoids_future_and_reports_sample_age(tmp_path):
    db = tmp_path / 'sampling.db'
    make_offset_db(db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match='匹配时间戳不足'):
            fuse_sources(conn, ['anchor', 'sensor'])
        nearest, near_meta = fuse_sources(conn, ['anchor', 'sensor'], tolerance_seconds=1.0)
        causal, causal_meta = fuse_sources(conn, ['anchor', 'sensor'],
                                           tolerance_seconds=1.0, alignment_method='previous')
    assert len(nearest) == 12
    assert len(causal) == 11
    assert causal[0]['timestamp'] == '2026-06-01T00:00:01'
    assert causal[0]['source_2'] == 20.0  # observed at 00:00:00.400, never at future 00:00:01.400
    assert causal_meta['alignment_policy'] == 'previous_with_tolerance'
    assert causal_meta['alignment_method'] == 'previous'
    assert causal_meta['offsets']['sensor']['max_seconds'] == pytest.approx(0.6, abs=1e-5)
    assert near_meta['alignment_policy'] == 'nearest_unique_with_tolerance'


def test_causal_alignment_max_age_is_enforced(tmp_path):
    db = tmp_path / 'sampling.db'
    make_offset_db(db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match='匹配时间戳不足'):
            fuse_sources(conn, ['anchor', 'sensor'], tolerance_seconds=.1,
                         alignment_method='previous')
        with pytest.raises(ValueError, match='同步方式'):
            fuse_sources(conn, ['anchor', 'sensor'], tolerance_seconds=1, alignment_method='guess')


def test_training_contract_carries_causal_policy(tmp_path):
    db = tmp_path / 'sampling.db'
    make_offset_db(db)
    result = TrainingPipeline(db, limit=0, train_ratio=.75).run('three_sigma', table='anchor',
        persist_model=False, data_contract={
            'feature_columns': ['value', 'source_2'],
            'resolved_source_tables': ['anchor', 'sensor'],
            'source_tables': ['anchor', 'sensor'],
            'alignment_tolerance_seconds': 1.0,
            'alignment_method': 'previous',
            'time_selection': {},
            'model_input_scaling': {'enabled': True, 'method': 'min_max'},
        })
    assert result.data_contract['alignment']['alignment_method'] == 'previous'
    assert result.data_contract['alignment']['aligned_count'] == 11


def test_shared_picker_present_in_all_three_flows():
    web = ROOT / 'web/visualization'
    picker = (web / 'resource-picker.js').read_text(encoding='utf-8')
    browser_html = (web / 'browser.html').read_text(encoding='utf-8')
    runner_html = (web / 'runner.html').read_text(encoding='utf-8')
    browser_js = (web / 'browser.js').read_text(encoding='utf-8')
    runner_js = (web / 'runner.js').read_text(encoding='utf-8')
    assert 'window.AASResourcePicker={open:mount}' in picker
    assert 'selection order' in picker.lower()
    for key in ('chooseBrowserResources',):
        assert f'id="{key}"' in browser_html
        assert key in browser_js
    for key in ('chooseTrainResources', 'chooseEvalResources', 'chooseRigorousTestResources'):
        assert f'id="{key}"' in runner_html
        assert key in runner_js
    assert 'alignmentMethod' in runner_html and "params.set('method'" in runner_js
    assert 'alignmentMethod: els.alignmentMethod?.value' in runner_js
