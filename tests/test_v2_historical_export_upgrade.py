"""Saved sparse historical simulations must export complete frame-ordered CSV."""
import csv
import hashlib
import io
import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.test_v2_simulation_hierarchical_phase16 import source_root, params
from scripts.server.v2_simulation_injection import (
    preview_v2_injection, save_v2_injection, preview_injection,
)


def _saved(source_root):
    root, db, _ = source_root
    from scripts.server.v2_simulation_injection import v2_source_catalog
    cfg = params(v2_source_catalog(root))
    snapshot = preview_v2_injection(root, cfg)
    saved = save_v2_injection(root, {**cfg, 'preview_sha256': snapshot['preview_sha256']})
    return root, db, saved


def test_full_saved_record_csv_preserves_order_label_and_source(source_root):
    from scripts.server.v2_simulation_injection import export_injection_csv
    root, db, saved = _saved(source_root)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    limited = preview_injection(root, saved['id'], max_rows=10)
    assert len(limited['points']) == 10
    raw = export_injection_csv(root, saved['id'])
    content = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    assert len(content) == saved['sampling_rows'] == 600
    assert [int(row['frame']) for row in content] == list(range(1, 601))
    assert sum(int(row['label']) for row in content) == 400
    assert content[0]['source_column'] == saved['variable']
    assert content[0]['source_record'] == f'v2:{saved["id"]}'
    assert content[199]['label'] == '0' and content[200]['label'] == '1'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_saved_record_csv_rejects_unknown_or_invalid_id(source_root):
    from scripts.server.v2_simulation_injection import export_injection_csv
    root, _, saved = _saved(source_root)
    for invalid in (0, -1, True, '1', saved['id']+1000):
        with pytest.raises(ValueError):
            export_injection_csv(root, invalid)


def test_real_http_saved_csv_download_is_authenticated(source_root, monkeypatch):
    from scripts import visualization_server as app
    from scripts.server.home_auth import HomeAuth
    root, _, saved = _saved(source_root)
    monkeypatch.setattr(app, 'ROOT', root)
    monkeypatch.setattr(app, 'HOME_AUTH', HomeAuth(root))
    server = ThreadingHTTPServer(('127.0.0.1', 0), app.DashboardHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    host = f'http://127.0.0.1:{server.server_port}'
    try:
        def post(path, data):
            req = Request(host+path, data=json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(req, timeout=8) as resp:
                return json.load(resp), resp.headers.get('Set-Cookie', '')
        with pytest.raises(HTTPError) as denied:
            urlopen(host+f'/api/v2/simulation/export?id={saved["id"]}', timeout=8)
        assert denied.value.code in (401,403)
        post('/api/auth/setup', {'username': 'admin', 'password': 'strong-password-for-test'})
        _, cookie = post('/api/auth/login', {'username': 'admin', 'password': 'strong-password-for-test'})
        req = Request(host+f'/api/v2/simulation/export?id={saved["id"]}',
                      headers={'Cookie': cookie.split(';', 1)[0]})
        with urlopen(req, timeout=8) as resp:
            assert resp.headers['Content-Type'].startswith('text/csv')
            assert len(list(csv.DictReader(io.StringIO(resp.read().decode('utf-8'))))) == 600
    finally:
        server.shutdown(); worker.join(timeout=5); server.server_close()
