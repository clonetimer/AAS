from __future__ import annotations

import hashlib
import sqlite3

from scripts.server import v2_csv_workspace as sim


def _import(root):
    raw = (b'time_s,label,gyro_x,voltage,note\n'
           b'0,0,1,28,base-a\n1,0,2,28,base-b\n'
           b'2,1,3,27,base-c\n3,1,4,27,base-d\n')
    return sim.import_csv(root, raw, 'user-sim.csv', csv_encoding='utf-8',
                          csv_delimiter=',', format_confirmed=True)


def test_r2_r3_simulation_chain_survives_reopen_and_never_touches_history(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'historical-db-sentinel')
    before = hashlib.sha256(history.read_bytes()).hexdigest()

    original = _import(tmp_path)
    cfg = {'dataset_id': original['id'], 'column': 'gyro_x',
           'fault_type': 'bias', 'start_frame': 2, 'end_frame': 3,
           'magnitude': 4}
    preview = sim.preview_fault(tmp_path, cfg)
    v1 = sim.save_fault(tmp_path, {**cfg, 'preview_sha256': preview['preview_sha256']})

    cfg2 = {'dataset_id': v1['id'], 'column': 'voltage',
            'fault_type': 'stuck', 'start_frame': 3, 'end_frame': 4,
            'magnitude': 0}
    preview2 = sim.preview_fault(tmp_path, cfg2)
    v2 = sim.save_fault(tmp_path, {**cfg2, 'preview_sha256': preview2['preview_sha256']})

    reopened = sim.get_dataset(tmp_path, v2['id'])
    assert reopened['version'] == 2
    assert reopened['parent_id'] == v1['id']
    assert [event['fault_type'] for event in reopened['events']] == ['bias', 'stuck']
    assert len(reopened['frames']) == 4
    assert sim.get_dataset(tmp_path, original['id'])['version'] == 0
    assert sim.list_ancestors(tmp_path, v2['id'])['ancestors'][0]['id'] == v1['id']

    rollback_preview = sim.preview_rollback(tmp_path, v2['id'], original['id'])
    v3 = sim.rollback_to_ancestor(tmp_path, {
        'dataset_id': v2['id'], 'ancestor_id': original['id'],
        'preview_sha256': rollback_preview['preview_sha256']})
    assert v3['version'] == 3
    assert v3['parent_id'] == v2['id']
    assert history.read_bytes() == b'historical-db-sentinel'
    assert hashlib.sha256(history.read_bytes()).hexdigest() == before

    with sqlite3.connect(sim._db_path(tmp_path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_csv_datasets').fetchone()[0] == 4


def test_r3_simulation_database_is_distinct_from_historical_database(tmp_path):
    history = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'historical')
    sim.import_csv(tmp_path, b'time_s,x\n0,1\n1,2\n', 'x.csv',
                   csv_encoding='utf-8', csv_delimiter=',', format_confirmed=True)
    assert sim._db_path(tmp_path).resolve() != history.resolve()
    assert history.read_bytes() == b'historical'


def test_r2_explicit_delete_removes_selected_lineage_only(tmp_path):
    first = _import(tmp_path)
    cfg = {'dataset_id': first['id'], 'column': 'gyro_x',
           'fault_type': 'bias', 'start_frame': 1, 'end_frame': 2, 'magnitude': 1}
    preview = sim.preview_fault(tmp_path, cfg)
    child = sim.save_fault(tmp_path, {**cfg, 'preview_sha256': preview['preview_sha256']})
    unrelated = sim.import_csv(tmp_path, b'time_s,x\n0,9\n1,10\n', 'other.csv',
                               csv_encoding='utf-8', csv_delimiter=',', format_confirmed=True)
    try:
        sim.delete_dataset(tmp_path, child['id'], confirm=False)
    except ValueError:
        pass
    else:
        raise AssertionError('simulation CSV deletion must require confirmation')
    deleted = sim.delete_dataset(tmp_path, child['id'], confirm=True)
    assert deleted['deleted_versions'] == 2
    try:
        sim.get_dataset(tmp_path, first['id'])
    except ValueError:
        pass
    else:
        raise AssertionError('deleting one version must remove its complete lineage')
    assert sim.get_dataset(tmp_path, unrelated['id'])['id'] == unrelated['id']
