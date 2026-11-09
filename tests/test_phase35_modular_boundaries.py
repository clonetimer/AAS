"""Phase 35: modular POST dispatch and explicit runtime adapters."""
from __future__ import annotations

import json
from contextlib import closing
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import visualization_server as app
from scripts.server.handler_post import EXTENSION_POST_PATHS
from tests.http_test_handler import AuthorizedTestHandler


def test_post_route_domains_are_explicit_and_no_legacy_training_activation():
    assert '/api/plugins/install' in EXTENSION_POST_PATHS
    assert '/api/preview-algorithm' in EXTENSION_POST_PATHS
    assert '/api/train-model' not in EXTENSION_POST_PATHS
    assert callable(app.publish_training_experiment)
    assert callable(app.execute_fault_injection)
    assert callable(app.get_model)


def test_model_registry_mutable_paths_retained(tmp_path, monkeypatch):
    model_dir = tmp_path / 'models'
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app, 'MODELS_DIR', model_dir)
    monkeypatch.setattr(app, 'MODEL_DB_PATH', model_dir / 'model.db')
    app.ensure_model_db()
    assert (model_dir / 'model.db').is_file()
    with sqlite3.connect(app.MODEL_DB_PATH) as conn:
        conn.execute('''INSERT INTO models (
            experiment_id, model_name, algorithm, dataset, table_name,
            created_at, published_at, file_path, metrics_json, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            ('run-1', 'test-model', 'zscore', 'aas_telemetry_v2.sqlite',
             'v2_channels', '2026-10-23', '2026-10-23',
             'models/model_run-1', '{}', '{}'))
    records = app.get_models(limit=10)
    assert len(records) == 1
    model_id = records[0]['id']
    assert app.get_model_by_experiment_id('run-1')['id'] == model_id
    assert app.update_model_status(model_id, 'evaluated')['status'] == 'success'
    assert app.get_model(model_id)['evaluation_count'] == 1
    assert app.get_model(model_id)['status'] == 'evaluated'


def test_simulation_compatibility_uses_current_path(tmp_path, monkeypatch):
    sim = tmp_path / 'simulation.db'
    src = tmp_path / 'history_sample.db'
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app, 'SIMULATION_DB_PATH', sim)
    monkeypatch.setattr(app, 'HISTORY_SAMPLE_DB_PATH', src)
    assert app.get_simulation_status()['exists'] is False
    # sqlite3.Connection.__exit__ commits but DOES NOT close the handle.
    # On Windows a still-open test fixture rightfully blocks file deletion.
    with closing(sqlite3.connect(sim)) as conn:
        with conn:
            conn.execute('CREATE TABLE source_table (timestamp REAL, value REAL)')
            conn.execute('INSERT INTO source_table VALUES (1.0, 3.0)')
    assert app.get_simulation_status()['original_tables'] == 1
    assert [x['name'] for x in app.get_simulation_tables()] == ['source_table']
    assert app.delete_simulation_db()['deleted'] is True
    assert not sim.exists()


def test_post_endpoints_are_live_and_unrecognized_stays_404():
    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        for path, payload, expected in [
            ('/api/models', {}, 400),
            ('/api/preview-algorithm', {}, 400),
            ('/api/install-dependency', {}, 400),
            ('/api/import-algorithm', {}, 400),
            ('/api/no-such-route', {}, 404),
            ('/api/train-model', {}, 410),
            ('/api/simulation/preview', {}, 410),
            ('/api/backup/create', {}, 410),
        ]:
            req = Request(base + path, json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
            try:
                response = urlopen(req, timeout=5)
            except HTTPError as exc:
                response = exc
            with response:
                actual = response.status
                result = json.loads(response.read())
            assert actual == expected, (path, actual, result)
            assert isinstance(result, dict)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
