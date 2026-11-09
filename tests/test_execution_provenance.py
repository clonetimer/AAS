"""Execution provenance: no browser-authored result can impersonate a run."""
import gzip
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import visualization_server as app
from scripts.server.execution_provenance import (
    ExecutionRegistry, canonical_bytes, data_fingerprint, sha256_bytes,
)
from scripts.verify_experiment import verify
from tests.http_test_handler import AuthorizedTestHandler


def test_receipts_are_single_use_typed_and_expire():
    registry = ExecutionRegistry(ttl_seconds=1, max_pending=2)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        receipt = registry.issue(kind='training', config={'table': 'x'}, result={'metrics': {'f1': 1}},
                                 identity={'experiment_id': 'test'}, root=root)
        try:
            registry.consume(receipt, 'evaluation')
            assert False, 'wrong type should be rejected'
        except ValueError:
            pass
        record = registry.consume(receipt, 'training')
        assert record['result']['metrics']['f1'] == 1
        try:
            registry.consume(receipt, 'training')
            assert False, 'replay should be rejected'
        except ValueError:
            pass
        expired = registry.issue(kind='training', config={}, result={}, identity={}, root=root)
        registry._pending[expired]['issued'] -= 10  # pylint: disable=protected-access
        try:
            registry.consume(expired, 'training')
            assert False, 'expired should be rejected'
        except ValueError:
            pass


def test_data_fingerprint_detects_row_changes():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'sample.db'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE telemetry (timestamp TEXT, value REAL, label INTEGER)')
            connection.execute("INSERT INTO telemetry VALUES ('t0', 1.2, 0)")
        first = data_fingerprint(root, db, {'table': 'telemetry'})
        assert first['status'] == 'complete'
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE telemetry SET value = 2.5")
        second = data_fingerprint(root, db, {'table': 'telemetry'})
        assert first['tables'][0]['sha256'] != second['tables'][0]['sha256']
        assert data_fingerprint(root, root.parent / 'bad.db', {'table': 'telemetry'})['status'] == 'unavailable'


def test_actual_evaluation_http_receipt_prevents_forged_history():
    class Detector:
        def score(self, samples):
            return samples

    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'datasets' / 'sample.db'
        db.parent.mkdir()
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE telemetry(timestamp REAL,value REAL,label INTEGER)')
            connection.executemany('INSERT INTO telemetry VALUES (?,?,?)',
                                   [(0, 0.1, 0), (1, 0.5, 0), (2, 5.0, 1)])
        models = root / 'models' / 'unit'
        models.mkdir(parents=True)
        (models / 'model.json').write_text('{"sample": 1}', encoding='utf-8')
        result = {
            'rows': [{'timestamp': 0, 'label': 0}, {'timestamp': 1, 'label': 0},
                     {'timestamp': 2, 'label': 1}],
            'threshold': [0.1, 0.5, 5.0], 'decision_threshold': 2.0,
            'pred_labels': [0, 0, 1], 'metrics': {'accuracy': 1.0},
        }
        registry = ExecutionRegistry()
        server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def request(path, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            req = Request(base + path, data=data, headers={'Content-Type': 'application/json'})
            try:
                with urlopen(req, timeout=10) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            with (patch.object(app, 'ROOT', root),
                  patch.object(app, 'EXECUTION_REGISTRY', registry),
                  patch.object(app, 'get_model', return_value={
                      'algorithm': 'zscore', 'model_name': 'demo', 'file_path': 'models/unit',
                  }), patch.object(app, 'resolve_managed_model_dir', return_value=models),
                  patch.object(app, 'load_model', return_value=Detector()),
                  patch.object(app, 'load_model_config', return_value={}),
                  patch.object(app, 'get_model_input_sources', create=True, return_value={}),
                  patch.object(app, 'update_model_evaluation'),
                  patch.object(app, 'run_evaluation', return_value={'result': result})):
                # Explicit backend evaluation, not a synthetic client-side token.
                code, executed = request('/api/evaluate-model', {
                    'model_id': 3, 'dataset': 'datasets/sample.db', 'table': 'telemetry'})
                assert code == 200, executed
                token = executed['execution_receipt']
                assert len(token) >= 30
                assert request('/api/save-experiment', {'type': 'evaluation'})[0] == 400
                forged = {'type': 'evaluation', 'execution_receipt': token,
                          'result': {'metrics': {'accuracy': 0.0}, 'threshold': [999]},
                          'config': {'dataset': '../../evil.db', 'table': 'forged'},
                          'model_id': 999, 'model_name': 'forged'}
                code, saved = request('/api/save-experiment', forged)
                assert code == 200, saved
                assert request('/api/save-experiment', forged)[0] == 400
                summary = root / 'outputs' / ('experiment_' + saved['experiment_id']) / 'reports' / 'summary.json'
                content = json.loads(summary.read_text(encoding='utf-8'))
                assert content['results'][0]['metrics']['accuracy'] == 1.0
                assert content['evaluation']['model_id'] == 3
                assert content['evaluation']['eval_table'] == 'telemetry'
                assert content['provenance']['authority'] == 'server_execution'
                assert content['provenance']['input_before']['status'] == 'complete'
                assert content['provenance']['input_changed_during_execution'] is False
                assert content['provenance']['model_artifacts']['status'] == 'complete'
                artifact = summary.parent / 'execution_result.json.gz'
                with gzip.open(artifact, 'rb') as handle:
                    recorded = json.load(handle)
                assert sha256_bytes(canonical_bytes(recorded)) == content['provenance']['result_sha256']
                assert verify(summary)['status'] == 'verified_archive_digest'
                saved_config = json.loads((summary.parent / 'execution_config.json').read_text(encoding='utf-8'))
                assert saved_config['table'] == 'telemetry'
                assert content['provenance']['config_artifact'] == 'reports/execution_config.json'
                # Recovered API also exposes the evidence metadata.
                code, details = request('/api/experiment-details?path=' +
                                        f'outputs/experiment_{saved["experiment_id"]}/reports/summary.json')
                assert code == 200, details
                assert details['provenance']['authority'] == 'server_execution'
                with gzip.open(artifact, 'wb') as handle:
                    handle.write(b'{}')
                assert verify(summary)['status'] == 'mismatch'
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()


def test_http_training_completion_receipt_archives_server_result():
    """Training submission -> async completed task -> trusted history archive."""
    import time
    with TemporaryDirectory() as temp:
        root = Path(temp)
        db = root / 'datasets' / 'sample.db'
        db.parent.mkdir()
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE train (timestamp REAL,value REAL,label INTEGER)')
            connection.executemany('INSERT INTO train VALUES (?,?,?)',
                                   [(i, float(i), 0) for i in range(8)])
        model_dir = root / 'models' / 'training_test'
        model_dir.mkdir(parents=True)
        (model_dir / 'config.json').write_text('{"algorithm":"zscore"}', encoding='utf-8')
        result = {'rows': [{'timestamp': i, 'label': 0} for i in range(8)],
                  'threshold': [float(i) for i in range(8)],
                  'pred_labels': [0] * 8, 'decision_threshold': 5.0,
                  'metrics': {'accuracy': 0.75}}
        fake_summary = {'experiment_id': 'trusted_run_123', 'result': result,
                        'model_info': {'model_dir': 'models/training_test',
                                       'model_file_path': 'models/training_test/config.json'}}
        registry = ExecutionRegistry()
        server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def request(path, payload=None):
            req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response:
                return json.load(response)

        try:
            with (patch.object(app, 'ROOT', root),
                  patch.object(app, 'EXECUTION_REGISTRY', registry),
                  patch.object(app, 'TRAINING_TASKS', {}),
                  patch.object(app, 'run_evaluation', return_value=fake_summary),
                  patch.object(app, 'resolve_managed_database_path', return_value=db),
                  patch.object(app, 'sqlite_tables', return_value=[
                      {'name': 'train', 'data_role': 'train', 'role_valid': True,
                       'is_single_measurement': True,
                       'columns': ['timestamp', 'value', 'label']}]),
                  patch.object(app, 'resolve_managed_model_dir', return_value=model_dir)):
                accepted = request('/api/train-model', {'dataset': 'datasets/sample.db',
                    'table': 'train', 'algorithm': 'zscore', 'trainRatio': 0.75})
                assert accepted['status'] == 'accepted'
                final = None
                for _ in range(100):
                    final = request('/api/train-status?task_id=' + accepted['task_id'])
                    if final['status'] != 'running':
                        break
                    time.sleep(0.05)
                assert final['status'] == 'completed', final
                receipt = final['result']['execution_receipt']
                assert receipt
                archived = request('/api/save-experiment', {
                    'type': 'training', 'execution_receipt': receipt,
                    'experiment_id': 'attacker_overwrite',
                    'result': {'metrics': {'accuracy': 0.0}}})
                assert archived['experiment_id'] == 'trusted_run_123'
                summary = root / 'outputs/experiment_trusted_run_123/reports/summary.json'
                saved = json.loads(summary.read_text(encoding='utf-8'))
                assert saved['results'][0]['metrics']['accuracy'] == 0.75
                assert saved['provenance']['input_before']['status'] == 'complete'
                assert saved['provenance']['model_artifacts']['status'] == 'complete'
                assert verify(summary)['status'] == 'verified_archive_digest'
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
