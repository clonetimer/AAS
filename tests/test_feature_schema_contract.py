"""Feature-schema contract: canonical column order, identity digest, drift detection."""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.annotate_uploaded_targets_v2 import SCHEMA
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.feature_schema import (INTEGRITY_BOUND, assert_feature_schema_matches,
                                           build_feature_schema, schema_digest,
                                           stored_feature_schema, verify_stored_feature_schema)
from scripts.server.v2_run_dataset import load_selection, validate_training_selection

KEYS = ['adcs.rw.speed_rad_s', 'adcs.rw.current_a']


@pytest.fixture
def labeled(tmp_path):
    db = tmp_path / 'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as c:
        create_v2(c)
        c.executescript(SCHEMA)
        c.execute('''CREATE TABLE v2_segment_clock(segment_id INTEGER PRIMARY KEY,
                reference TEXT NOT NULL, unit TEXT NOT NULL, declared_by TEXT)''')
        for rid, split, run_role in [(1, 'train', 'NORMAL'), (2, 'train', 'NORMAL'),
                                     (3, 'validation', 'NORMAL'), (4, 'validation', 'FAULT')]:
            namespace = f'run_{rid}'
            c.execute('''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,
                    imported_utc,source_table_count,source_row_count) VALUES (?,?,?,?,?,?,?,?)''',
                      (rid, namespace, f'run{rid}.csv.gz', f'hash{rid}', 30, '2026-01-01Z', 2, 6))
            c.execute('''INSERT INTO v2_run_annotations(source_id,token,model_alias,model_identity_kind,
                    split,group_id,run_role,onset_s,normal_frames,fault_frames,annotation_scope,
                    metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
                    VALUES (?,?,?,'synthetic_alias',?,?,?,?,?,?,'simulation_run',1,?,?,?)''',
                      (rid, f'run{rid}', f'SIM-SAT-{rid:03d}', split, f'group{rid}', run_role,
                       4 if run_role == 'FAULT' else None, 4 if run_role == 'FAULT' else 6,
                       2 if run_role == 'FAULT' else 0, f'target_hash_{rid}', 'ziphash', 'test fixture'))
            for fidx, key in enumerate(KEYS):
                cid = (rid - 1) * 2 + fidx + 1
                c.execute('''INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit)
                        VALUES(?,?,?,?,?,?,?)''',
                          (cid, namespace, f'SIM-SAT-{rid:03d}', 'adcs', 'reaction_wheel',
                           key.rsplit('.', 1)[-1], 'rad/s' if fidx == 0 else 'A'))
                c.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,
                    role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count)
                    VALUES (?,?,?,?,'telemetry','simulation','source_verified','numeric_unspecified',1,'[]',6)''',
                          (cid, rid, key, cid))
                c.execute('INSERT INTO v2_segment_clock VALUES(?,?,?,?)',
                          (cid, f'elapsed_run:run{rid}', 's', 'test'))
                for t in range(6):
                    label = 1 if run_role == 'FAULT' and t >= 4 else 0
                    c.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
                        time_domain,time_sort_num,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                              (cid, cid, t, str(t), 'numeric_unspecified', float(t), rid * 10 + t + fidx,
                               label, str(label), str(label), '{}'))
    return db


def choose(ids, keys=None, start=None, end=None):
    return {'source_ids': ids, 'feature_keys': keys or list(KEYS),
            'start': start, 'end': end, 'method': 'exact', 'tolerance_seconds': 0}


def _train_meta(db, keys):
    _, meta = load_selection(db, selection=choose([1, 2], start=2, end=4, keys=keys), role='train')
    return meta


def test_training_fixes_canonical_column_order(labeled):
    meta = _train_meta(labeled, list(KEYS))
    assert meta['feature_keys'] == sorted(KEYS)
    schema, digest = stored_feature_schema(meta)
    assert schema['keys'] == sorted(KEYS)
    assert schema['bindings'] == {'value': 'adcs.rw.current_a',
                                  'source_2': 'adcs.rw.speed_rad_s'}
    assert digest == schema_digest(schema) and len(digest) == 64
    assert schema['order_policy'] == 'lexicographic'


def test_click_order_cannot_change_the_schema(labeled):
    forward = _train_meta(labeled, list(KEYS))
    backward = _train_meta(labeled, list(reversed(KEYS)))
    assert forward['feature_keys'] == backward['feature_keys']
    assert forward['schema_sha256'] == backward['schema_sha256']


def test_validate_training_selection_carries_the_schema(labeled):
    meta = validate_training_selection(labeled, {'dataset_selection': choose([1, 2], start=2, end=4)})
    assert verify_stored_feature_schema(meta) == meta['feature_schema']


def test_evaluation_keeps_the_published_order(labeled):
    trained = _train_meta(labeled, list(KEYS))
    # Evaluation must not re-sort; a legacy model trained in click order stays usable.
    rows, meta = load_selection(labeled, selection=choose([3], keys=list(KEYS), start=3, end=5), role='test',
                                expected={'feature_keys': list(KEYS), 'train_source_ids': [1, 2],
                                          'train_groups': ['group1', 'group2'],
                                          'feature_schema': None})
    assert meta['feature_keys'] == list(KEYS)
    assert rows[0]['value'] == 33 and rows[0]['source_2'] == 34
    assert trained['schema_sha256'] != meta['schema_sha256']  # different column identity


def test_schema_mismatch_is_rejected(labeled):
    trained = _train_meta(labeled, list(KEYS))
    with pytest.raises(ValueError, match='第 1 列参量变化|不一致'):
        load_selection(labeled, selection=choose([3], keys=list(KEYS), start=3, end=5), role='test',
                       expected={'feature_keys': list(KEYS), 'train_source_ids': [1, 2],
                                 'train_groups': ['group1', 'group2'],
                                 'feature_schema': trained['feature_schema']})


def test_semantic_drift_in_evaluation_run_is_rejected(labeled):
    meta = _train_meta(labeled, sorted(KEYS))
    saved = build_feature_schema(meta['feature_keys'], meta['feature_schema']['semantics'])
    live = build_feature_schema(meta['feature_keys'],
                                [['adcs', 'reaction_wheel_x', 'current_a', 'A'],
                                 ['adcs', 'reaction_wheel', 'speed_rad_s', 'rad/s']])
    with pytest.raises(ValueError, match='物理含义变化'):
        assert_feature_schema_matches(saved, live)


def test_legacy_contract_without_schema_is_not_blocked(labeled):
    assert_feature_schema_matches(None, build_feature_schema(KEYS))
    assert verify_stored_feature_schema({'feature_columns': ['value', 'source_2']}) is None


def test_tampered_contract_is_rejected():
    contract = {'feature_columns': ['value', 'source_2']}
    schema = build_feature_schema(KEYS)
    contract['feature_schema'] = schema
    contract['schema_sha256'] = schema_digest(schema)
    assert verify_stored_feature_schema(contract) == schema

    swapped = dict(schema, bindings={'source_2': 'adcs.rw.speed_rad_s', 'value': 'adcs.rw.current_a'})
    tampered = {'feature_columns': ['value', 'source_2'],
                'feature_schema': swapped, 'schema_sha256': contract['schema_sha256']}
    with pytest.raises(ValueError, match='指纹|绑定'):
        verify_stored_feature_schema(tampered)


def test_backfill_reconstructs_a_legacy_artifact(tmp_path):
    from scripts.backfill_feature_schema import backfill

    model_dir = tmp_path / 'models' / 'model_legacy'
    model_dir.mkdir(parents=True)
    contract = {
        'version': 7, 'feature_columns': ['value', 'source_2'],
        'v2_run_selection': {'feature_keys': list(KEYS), 'source_ids': [1, 2],
                             'method': 'exact', 'tolerance_seconds': 0},
        'v2_run_manifest': {'runs': [{'group_id': 'group1', 'semantic_features': [
            ['adcs', 'reaction_wheel', 'speed_rad_s', 'rad/s'],
            ['adcs', 'reaction_wheel', 'current_a', 'A']]}]},
    }
    config_path = model_dir / 'config.json'
    config_path.write_text(json.dumps({'algorithm': 'x', 'config': {'data_contract': contract}}),
                           encoding='utf-8')

    dry = backfill(tmp_path / 'models', apply=False)
    assert dry['planned'] and not config_path.with_suffix('.json.bak').exists()

    assert backfill(tmp_path / 'models', apply=True)['applied']
    written = json.loads(config_path.read_text(encoding='utf-8'))['config']['data_contract']
    schema = verify_stored_feature_schema(written)
    assert schema['keys'] == list(KEYS)                     # original order preserved
    assert schema['order_policy'] == 'as_trained'
    assert written['schema_sha256'] == schema_digest(schema)
    # The backfilled identity must accept the live test-run layout for the same keys.
    assert schema_digest(schema) == schema_digest(build_feature_schema(
        list(KEYS), contract['v2_run_manifest']['runs'][0]['semantic_features']))
    assert backfill(tmp_path / 'models', apply=False)['already_bound']


def test_training_persists_a_self_consistent_schema(tmp_path, labeled):
    from scripts.core.model_manager import set_models_dir
    from scripts.core.training import TrainingPipeline

    contract = {
        'version': 7, 'feature_columns': ['value', 'source_2'], 'v2_role': 'train',
        'v2_run_selection': {'source_ids': [1, 2], 'feature_keys': list(KEYS),
                             'method': 'exact', 'tolerance_seconds': 0},
        'v2_label_target': {'scope': 'run'},
        'model_input_scaling': {'enabled': True, 'method': 'min_max'},
        'time_selection': {'column': 'timestamp', 'start': None, 'end': None},
    }
    set_models_dir(tmp_path / 'model_artifacts')
    trained = TrainingPipeline(labeled, limit=0, train_ratio=0.5).run(
        'three_sigma', table='v2_channels', data_contract=contract)
    stored = json.loads((Path(trained.model_info['model_dir']) / 'config.json')
                        .read_text(encoding='utf-8'))['config']['data_contract']
    schema = verify_stored_feature_schema(stored)
    # The persisted selection must agree with the matrix the model was fit on.
    assert stored['v2_run_selection']['feature_keys'] == sorted(KEYS)
    assert schema['keys'] == sorted(KEYS)
    assert stored['schema_sha256'] == schema_digest(schema)


def test_runner_ui_states_the_order_is_fixed():
    root = Path(__file__).resolve().parents[1]
    js = (root / 'web/visualization/runner-v2.js').read_text(encoding='utf-8')
    assert '按参量名规范化，训练后不可调整' in js
    assert 'train?[...selectedFeatures].sort()' in js
    assert 'schema_bound' in js and 'schema_sha256' in js
