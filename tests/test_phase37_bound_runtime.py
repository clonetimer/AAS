"""Phase 37: per-server context, explicit dependency resolution, model write auth."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import pytest

from scripts import visualization_server as app
from scripts.server import handlers
from scripts.server.application_context import (
    HTTP_OPERATIONS, bind_http_context, context_from_runtime,
)
from scripts.server.home_auth import HomeAuth
from tests.http_test_handler import AuthorizedTestHandler


def _request(server, path, *, method='GET', body=None, origin=None):
    headers = {}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if origin is not None:
        headers['Origin'] = origin
    request = Request(f'http://127.0.0.1:{server.server_port}{path}',
                      data=body, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


class HttpServer:
    def __init__(self, handler, ctx):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        bind_http_context(self.server, ctx)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self.server

    def __exit__(self, *args):
        self.server.shutdown()
        self.thread.join(timeout=8)
        self.server.server_close()


def test_context_uses_reviewed_operations_and_blocks_arbitrary_attributes():
    context = context_from_runtime(app)
    assert context.ROOT == app.ROOT
    assert 'json_response' in HTTP_OPERATIONS
    assert callable(context.json_response)
    with pytest.raises(AttributeError, match='Unbound HTTP operation'):
        _ = context.unbound
    with pytest.raises(TypeError):
        context._operations['new_unreviewed_op'] = lambda: None


def test_bound_http_never_calls_legacy_main_resolution(monkeypatch, tmp_path):
    context = replace(context_from_runtime(app), ROOT=tmp_path)
    def must_not_be_called():
        raise AssertionError('Legacy runtime resolution invoked by a bound HTTP server')
    monkeypatch.setattr(handlers, '_get_main_module', must_not_be_called)
    with HttpServer(AuthorizedTestHandler, context) as server:
        assert _request(server, '/api/auth/status')[0] == 200
        status, result = _request(server, '/api/v2/simulation/fault-types')
        assert status == 200 and isinstance(result, dict)
        status, result = _request(server, '/api/models', method='POST', body=b'{}')
        assert status == 400 and isinstance(result, dict)
        assert _request(server, '/api/train-model', method='POST', body=b'{}')[0] == 410


def test_bound_servers_keep_distinct_auth_and_static_roots(tmp_path):
    root_a, root_b = tmp_path / 'a', tmp_path / 'b'
    for root in (root_a, root_b):
        (root / 'web').mkdir(parents=True)
    (root_a / 'web' / 'isolation.txt').write_text('server-a', encoding='utf8')
    (root_b / 'web' / 'isolation.txt').write_text('server-b', encoding='utf8')
    auth_a, auth_b = HomeAuth(root_a), HomeAuth(root_b)
    auth_a.setup('admin', 'test-password-very-long', '127.0.0.1')
    original = context_from_runtime(app)
    ctx_a = replace(original, ROOT=root_a, STATIC_DIR=root_a/'web', HOME_AUTH=auth_a)
    ctx_b = replace(original, ROOT=root_b, STATIC_DIR=root_b/'web', HOME_AUTH=auth_b)
    with HttpServer(AuthorizedTestHandler, ctx_a) as server_a, \
         HttpServer(AuthorizedTestHandler, ctx_b) as server_b:
        assert _request(server_a, '/api/auth/status')[1]['initialized'] is True
        assert _request(server_b, '/api/auth/status')[1]['initialized'] is False
        with urlopen(f'http://127.0.0.1:{server_a.server_port}/isolation.txt', timeout=8) as r:
            assert r.read() == b'server-a'
        with urlopen(f'http://127.0.0.1:{server_b.server_port}/isolation.txt', timeout=8) as r:
            assert r.read() == b'server-b'


def test_context_rebinding_is_rejected():
    ctx = context_from_runtime(app)
    with ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler) as server:
        bind_http_context(server, ctx)
        with pytest.raises(RuntimeError, match='already bound'):
            bind_http_context(server, ctx)


def test_model_mutation_methods_require_authentication_and_same_origin(tmp_path):
    """DELETE/PUT previously bypassed the centralized authentication guard."""
    ctx = replace(context_from_runtime(app), HOME_AUTH=HomeAuth(tmp_path))
    with HttpServer(handlers.DashboardHandler, ctx) as server:
        assert _request(server, '/api/models/1', method='DELETE')[0] == 401
        assert _request(server, '/api/models/1/status', method='PUT', body=b'{}')[0] == 401
        assert _request(server, '/api/models/1/status', method='PUT', body=b'{}',
                        origin='https://evil.example')[0] == 403


def test_bound_model_mutations_use_explicit_services_and_origin_guard(tmp_path):
    from types import MappingProxyType
    context = context_from_runtime(app)
    called = []
    operations = dict(context._operations)
    def update(model_id, status):
        called.append(('update', model_id, status))
        return {'status': 'success'}
    def delete(model_id):
        called.append(('delete', model_id))
        return {'status': 'success'}
    operations.update(update_model_status=update, delete_model=delete)
    context = replace(context, _operations=MappingProxyType(operations))
    with HttpServer(AuthorizedTestHandler, context) as server:
        assert _request(server, '/api/models/37/status', method='PUT',
                        body=b'{"status":"evaluated"}')[0] == 200
        assert _request(server, '/api/models/37', method='DELETE')[0] == 200
        assert _request(server, '/api/models/37', method='DELETE',
                        origin='https://another-host.example')[0] == 403
        assert called == [('update', 37, 'evaluated'), ('delete', 37)]


def test_plugin_catalog_uses_bound_project_root(monkeypatch, tmp_path):
    from scripts.server import handler_get
    inspected = []
    def catalog(root):
        inspected.append(root)
        return {'detectors': {'plugins': []}}
    monkeypatch.setattr(handler_get, 'collect_plugins', catalog)
    ctx = replace(context_from_runtime(app), ROOT=tmp_path)
    with HttpServer(AuthorizedTestHandler, ctx) as server:
        status, result = _request(server, '/api/plugins')
    assert status == 200 and 'plugins' in result
    assert inspected == [tmp_path]


def test_cli_bootstrap_binds_context_before_serving(monkeypatch):
    seen = []
    class FakeHTTPServer:
        def __init__(self, address, handler_class):
            self.address = address
            self.handler_class = handler_class
        def serve_forever(self):
            seen.append((self.address, self.handler_class, self.aas_application.ROOT))
        def server_close(self):
            seen.append('closed')
    monkeypatch.setattr(app, 'ThreadingHTTPServer', FakeHTTPServer)
    monkeypatch.setattr(app, 'setup_logging', lambda: {'server': {'host': '127.0.0.1', 'port': 0}})
    monkeypatch.setattr(app, 'parse_args', lambda: type('Args', (), {'host': None, 'port': None})())
    assert app.main() == 0
    assert seen == [(('127.0.0.1', 0), handlers.DashboardHandler, app.ROOT), 'closed']
