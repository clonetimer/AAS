"""Actual detector input snapshot and audit-boundary regression tests."""
import gzip
import json
import sqlite3
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest

from scripts import visualization_server as app
from scripts.core.data_loader import DataBundle
from scripts.core.input_evidence import (capture_stage, finish_evidence,
                                         verify_input_snapshot)
from scripts.server.execution_provenance import ExecutionRegistry
from scripts.verify_experiment import verify
from tests.http_test_handler import AuthorizedTestHandler


def small_bundle():
    rows = [
        {'timestamp': i, 'label': 0, 'value': float(i),
         'source_timestamp_1': i, 'source_timestamp_2': i-0.5,
         'source_label_1': 0, 'source_label_2': 0}
        for i in range(4)
    ]
    return DataBundle(train_rows=rows[:3], test_rows=rows[3:],
                      train_values=[[float(i), float(i+1)] for i in range(3)],
                      test_values=[[3., 4.]], mode='multi_table_ratio_split',
                      train_value_rows=rows[:3], test_value_rows=rows[3:],
                      metadata={'alignment': {'timeline_mode': 'fixed', 'grid_interval_seconds': 1}})


def evidence_bundle():
    bundle = small_bundle()
    raw = capture_stage(bundle, 'raw', ['value', 'source_2'])
    bundle.train_values = [[v/10 for v in row] for row in bundle.train_values]
    bundle.test_values = [[v/10 for v in row] for row in bundle.test_values]
    scale = {'enabled': True, 'method': 'min_max', 'parameters': {'test': 'fixture'}}
    model = capture_stage(bundle, 'model', ['value', 'source_2'])
    return finish_evidence(raw, model, bundle, ['value', 'source_2'], scale)


def test_snapshot_contains_precise_split_matrices_timestamps_and_fusion_lineage():
    evidence, snapshot = evidence_bundle()
    assert evidence['snapshot_status'] == 'complete'
    assert evidence['stages']['raw']['train']['rows'] == 3
    assert evidence['stages']['model']['test']['rows'] == 1
    assert snapshot['stages']['raw']['test'][0]['values'] == [3., 4.]
    assert snapshot['stages']['model']['test'][0]['values'] == [0.3, 0.4]
    assert snapshot['stages']['raw']['test'][0]['source_observations']['source_timestamp_2'] == 2.5
    assert verify_input_snapshot(snapshot, evidence)

    altered = deepcopy(snapshot)
    altered['stages']['model']['test'][0]['values'][1] = 0.99
    assert not verify_input_snapshot(altered, evidence)
    reordered = deepcopy(snapshot)
    reordered['feature_columns'].reverse()
    assert not verify_input_snapshot(reordered, evidence)


def test_large_snapshot_only_keeps_full_stream_digests():
    bundle = small_bundle()
    original, _ = capture_stage(bundle, 'raw', ['value', 'source_2'], snapshot_rows_limit=2)
    assert original['snapshot_status'] == 'digest_only_size_limit'
    assert len(original['splits']['train']['sha256']) == 64
    with pytest.raises(ValueError, match='dimension'):
        capture_stage(bundle, 'raw', ['value'])


def test_receipt_rejects_inconsistent_input_evidence():
    evidence, snapshot = evidence_bundle()
    with TemporaryDirectory() as directory:
        registry = ExecutionRegistry()
        registry.issue(kind='training', config={}, result={}, identity={}, root=Path(directory),
                       input_evidence=evidence, input_snapshot=snapshot)
        bad = deepcopy(snapshot)
        bad['stages']['raw']['train'][0]['timestamp'] = 100
        with pytest.raises(ValueError, match='快照'):
            registry.issue(kind='training', config={}, result={}, identity={}, root=Path(directory),
                           input_evidence=evidence, input_snapshot=bad)
        with pytest.raises(ValueError, match='缺少快照'):
            registry.issue(kind='training', config={}, result={}, identity={}, root=Path(directory),
                           input_evidence=evidence)


def test_http_archive_input_snapshot_and_detect_tamper():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'datasets' / 'sample.db'
        db.parent.mkdir()
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE test(timestamp REAL,value REAL,label INTEGER)')
            connection.execute('INSERT INTO test VALUES (1,2,0)')
        manifest, snapshot = evidence_bundle()
        registry = ExecutionRegistry()
        token = registry.issue(kind='evaluation', config={'dataset': 'datasets/sample.db', 'table': 'test'},
                               result={'metrics': {'accuracy': 1.0}, 'input_evidence': manifest},
                               identity={'model_id': 1, 'model_name': 'demo', 'model_algorithm': 'zscore'},
                               root=root, db_path=db, input_evidence=manifest, input_snapshot=snapshot)
        server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(app, 'ROOT', root), patch.object(app, 'EXECUTION_REGISTRY', registry):
                payload = json.dumps({'type': 'evaluation', 'execution_receipt': token}).encode()
                request = Request(f'http://127.0.0.1:{server.server_port}/api/save-experiment',
                                  data=payload, headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=8) as response:
                    saved = json.load(response)
                summary = root / f'outputs/experiment_{saved["experiment_id"]}/reports/summary.json'
                report = json.loads(summary.read_text(encoding='utf-8'))
                assert report['provenance']['effective_model_inputs'] == manifest
                assert report['provenance']['input_snapshot_artifact'] == 'reports/execution_inputs.json.gz'
                assert verify(summary)['input_snapshot_matches'] is True
                snapshot_file = summary.parent / 'execution_inputs.json.gz'
                with gzip.open(snapshot_file, 'rt', encoding='utf-8') as handle:
                    archived = json.load(handle)
                archived['stages']['model']['test'][0]['values'][0] = -100
                with gzip.open(snapshot_file, 'wb') as handle:
                    handle.write(json.dumps(archived).encode())
                assert verify(summary)['status'] == 'mismatch'
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()


def test_real_multirate_training_captures_actual_post_alignment_and_scaled_values():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'datasets' / 'sample.db'
        db.parent.mkdir()
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE temperature(timestamp REAL,value REAL,label INTEGER,model TEXT,subsystem TEXT,device TEXT,variable TEXT)')
            connection.execute('CREATE TABLE current(timestamp REAL,value REAL,label INTEGER,model TEXT,subsystem TEXT,device TEXT,variable TEXT)')
            connection.executemany('INSERT INTO temperature VALUES (?,?,0,?,?,?,?)',
                                   [(float(i * 2), 20 + i,'DEMO','ADCS','unit','temperature') for i in range(30)])
            connection.executemany('INSERT INTO current VALUES (?,?,0,?,?,?,?)',
                                   [(float(i * 2) - 0.5, 1 + i / 10,'DEMO','ADCS','unit','current') for i in range(30)])
        with patch.object(app, 'ROOT', root):
            summary = app.run_evaluation(
                dataset='datasets/sample.db', table='temperature', algorithm='zscore',
                source_tables=['temperature', 'current'],
                alignment_tolerance_seconds=1., alignment_method='previous',
                train_ratio=.7, persist_model=False)
        evidence = summary['result']['input_evidence']
        snapshot = summary['_input_snapshot']
        assert evidence['snapshot_status'] == 'complete'
        assert evidence['feature_columns'] == ['value', 'source_2']
        assert verify_input_snapshot(snapshot, evidence)
        sample = snapshot['stages']['raw']['train'][0]
        assert sample['source_observations']['source_timestamp_2'] == -0.5
        assert sample['values'] == [20., 1.]
        scaled = snapshot['stages']['model']['train'][0]
        assert scaled['values'] != sample['values']
        assert evidence['stages']['raw']['train']['sha256'] != evidence['stages']['model']['train']['sha256']
        assert evidence['stages']['raw']['test']['rows'] > 0


def test_scaler_changes_model_digest_but_not_aligned_raw_data():
    def recorded(scaler_factor):
        bundle = small_bundle()
        raw = capture_stage(bundle, 'raw', ['value', 'source_2'])
        bundle.train_values = [[v * scaler_factor for v in row] for row in bundle.train_values]
        bundle.test_values = [[v * scaler_factor for v in row] for row in bundle.test_values]
        model = capture_stage(bundle, 'model', ['value', 'source_2'])
        return finish_evidence(raw, model, bundle, ['value', 'source_2'],
                               {'method': 'custom_fixture', 'factor': scaler_factor})[0]

    first = recorded(.1)
    second = recorded(.2)
    assert first['stages']['raw'] == second['stages']['raw']
    assert first['stages']['model'] != second['stages']['model']
    assert first['scaling_sha256'] != second['scaling_sha256']


def test_combined_evidence_explicitly_declines_oversize_snapshot():
    bundle = small_bundle()
    raw = capture_stage(bundle, 'raw', ['value', 'source_2'], snapshot_rows_limit=1)
    model = capture_stage(bundle, 'model', ['value', 'source_2'], snapshot_rows_limit=1)
    evidence, snapshot = finish_evidence(raw, model, bundle, ['value', 'source_2'], {})
    assert evidence['snapshot_status'] == 'digest_only_size_limit'
    assert snapshot is None
    assert evidence['stages']['model']['test']['rows'] == 1
    assert 'snapshot_sha256' not in evidence
