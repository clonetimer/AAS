"""Interval ground-truth regressions. No user SQLite database is touched."""
import sqlite3

import pytest

from scripts.core.fault_intervals import (
    create_interval, delete_interval, list_intervals, load_lookup,
)
from scripts.core.multi_table_fusion import fuse_sources


def make_db(tmp_path):
    path = tmp_path / 'event_samples.db'
    with sqlite3.connect(path) as db:
        for t in ('a', 'b'):
            db.execute(f'''CREATE TABLE "{t}" (timestamp REAL, model TEXT,
             subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)''')
            db.executemany(f'INSERT INTO "{t}" VALUES (?,?,?,?,?,?,?)', [
                (float(i), 'S', 'ADCS', 'gyro', t, float(i), None) for i in range(10)
            ])
    return path


def add(db, table, start, end, label, **kw):
    return create_interval(db, table=table, start=start, end=end,
                           label=label, **kw)


def test_half_open_and_unknown_is_not_normal(tmp_path):
    with sqlite3.connect(make_db(tmp_path)) as db:
        add(db, 'a', 0, 4, 0)
        event = add(db, 'a', 4, 7, 1, fault_type='bearing')
        add(db, 'b', 0, 7, 0)
        with pytest.raises(ValueError, match='重叠'):
            add(db, 'a', 6, 8, 1)
        assert len(list_intervals(db, 'a')) == 2
        lookup = load_lookup(db, 'a')
        assert lookup.at(('numeric', 3)) == 0
        assert lookup.at(('numeric', 4)) == 1
        assert lookup.at(('numeric', 7)) is None
        rows, meta = fuse_sources(db, ['a', 'b'], label_alignment_method='interval')
        assert [r['timestamp'] for r in rows] == list(range(7))
        assert [r['label'] for r in rows] == [0, 0, 0, 0, 1, 1, 1]
        assert meta['unknown_label_count'] == 3
        assert meta['interval_semantics'] == '[start,end)'
        delete_interval(db, table='a', interval_id=event['id'])
        assert len(list_intervals(db, 'a')) == 1


def test_event_mapping_independent_from_measured_sample_and_future_reading(tmp_path):
    with sqlite3.connect(make_db(tmp_path)) as db:
        add(db, 'a', 0, 2, 0)
        add(db, 'a', 2, 6, 1)
        add(db, 'a', 6, 10, 0)
        add(db, 'b', 0, 10, 0)
        rows, meta = fuse_sources(db, ['a', 'b'], tolerance_seconds=2,
                                  alignment_method='previous', timeline_mode='fixed',
                                  grid_interval_seconds=.5, label_alignment_method='interval')
        at = next(r for r in rows if r['timestamp'] == 2.5)
        assert at['source_timestamp_1'] == 2.0
        assert at['source_label_1'] == 1
        assert at['label'] == 1
        assert meta['label_mapping_policy'] == 'explicit_intervals_unknown_excluded'


def test_no_annotations_rejected_no_guess(tmp_path):
    with sqlite3.connect(make_db(tmp_path)) as db:
        with pytest.raises(ValueError, match='没有标注'):
            fuse_sources(db, ['a','b'], label_alignment_method='interval')
        add(db, 'a', 0, 1, 0)
        add(db, 'b', 0, 1, 0)
        with pytest.raises(ValueError, match='不足'):
            fuse_sources(db, ['a','b'], label_alignment_method='interval')


@pytest.mark.parametrize('kw, message', [
    ({'start': 4, 'end': 4, 'label': 1}, '早于'),
    ({'start': 3, 'end': 2, 'label': 1}, '早于'),
    ({'start': 0, 'end': 1, 'label': None}, '只能'),
    ({'start': 0, 'end': 1, 'label': True}, '只能'),
    ({'start': 0, 'end': 1, 'label': 0, 'fault_type': 'leak'}, '正常区间'),
    ({'start': '2026-01-01T00:00:00Z', 'end': 2, 'label': 1}, '类型不一致'),
])
def test_reject_bad_annotations(tmp_path, kw, message):
    with sqlite3.connect(make_db(tmp_path)) as db:
        with pytest.raises(ValueError, match=message):
            create_interval(db, table='a', **kw)


def test_boundaries_and_ownership(tmp_path):
    with sqlite3.connect(make_db(tmp_path)) as db:
        with pytest.raises(ValueError, match='不存在'):
            add(db, 'missing', 0, 1, 1)
        event = add(db, 'a', 0, 2, 1)
        with pytest.raises(ValueError, match='不属于'):
            delete_interval(db, table='b', interval_id=event['id'])
        assert len(list_intervals(db,'a')) == 1
        with pytest.raises(ValueError, match='时间戳类型不一致'):
            add(db, 'a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:10Z', 1)


def test_update_interval_atomic_identity_and_reject_overlap(tmp_path):
    from scripts.core.fault_intervals import update_interval
    with sqlite3.connect(make_db(tmp_path)) as db:
        original = add(db, 'a', 0, 3, 0)
        add(db, 'a', 5, 8, 1, fault_type='bias')
        event_id = original['id']
        changed = update_interval(db, table='a', interval_id=event_id,
                                  start=1, end=4, label=0)
        assert changed['id'] == event_id
        with pytest.raises(ValueError, match='重叠'):
            update_interval(db, table='a', interval_id=event_id,
                            start=2, end=6, label=1, fault_type='drift')
        assert [(e['id'], e['start_timestamp'], e['end_timestamp']) for e in list_intervals(db, 'a')][0] == (event_id, '1', '4')


def test_single_table_interval_labels_override_rows_and_exclude_unknown(tmp_path):
    from scripts.core.data_loader import DataLoader
    with sqlite3.connect(make_db(tmp_path)) as db:
        add(db, 'a', 0, 6, 0)
        add(db, 'a', 6, 9, 1, fault_type='bias')
    path = tmp_path / 'event_samples.db'
    loader = DataLoader(path, train_ratio=.67)
    loader.label_alignment_method = 'interval'
    bundle = loader.load_and_split(table='a')
    assert len(bundle.train_rows) + len(bundle.test_rows) == 9
    assert [r['label'] for r in bundle.test_rows] == [1,1,1]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM a WHERE label IS NULL').fetchone()[0] == 10
