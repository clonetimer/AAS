"""Regression: shipped detectors are protected; genuine custom plugins can be removed."""
from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from core.plugin_runtime import PluginRuntime

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = (
    'torch_like_cusum', 'torch_like_ewma', 'torch_like_pca_spe',
    'torch_like_usad', 'torch_like_tranad',
)


def test_shipped_algorithms_identified_as_builtins():
    items = {x['name']: x for x in PluginRuntime(ROOT).collect()['detectors']['items']}
    for name in SHIPPED:
        item = items[name]
        assert item['builtin'] is True
        assert item['source'] == 'builtin'
        assert item['uninstallable'] is False
        assert item['managed_install'] is False


@pytest.mark.parametrize('force', [False, True])
def test_shipped_descriptors_cannot_be_uninstalled(force):
    runtime = PluginRuntime(ROOT)
    before = {name: (ROOT / 'configs' / 'detectors' / f'{name}.yaml').read_bytes() for name in SHIPPED}
    for name in SHIPPED:
        with pytest.raises(ValueError, match='内置插件'):
            runtime.uninstall('detectors', name, force=force)
    assert all((ROOT / 'configs' / 'detectors' / f'{n}.yaml').read_bytes() == val for n, val in before.items())


def test_plugin_uninstall_http_returns_json_and_custom_removal(tmp_path, monkeypatch):
    """The browser must receive a JSON error rather than a severed connection."""
    from scripts import visualization_server as main
    from tests.http_test_handler import AuthorizedTestHandler

    monkeypatch.setattr(main, 'ROOT', tmp_path)
    cfg = tmp_path / 'configs' / 'fault_types'
    cfg.mkdir(parents=True)
    custom = cfg / 'uninstall_test_custom.yaml'
    custom.write_text('name: uninstall_test_custom\nclass_path: src.fault_plugins.noop.Demo\n', encoding='utf-8')
    shipped_dir = tmp_path / 'configs' / 'detectors'
    shipped_dir.mkdir(parents=True)
    builtin = shipped_dir / 'torch_like_usad.yaml'
    builtin.write_text('name: torch_like_usad\nclass_path: detectors.torch_like_neural.TorchLikeUSADDetector\n', encoding='utf-8')

    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(body):
        request = Request(
            f'http://127.0.0.1:{server.server_port}/api/plugins/uninstall',
            data=json.dumps(body).encode('utf-8'), method='POST',
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            return response.code, json.loads(response.read())

    try:
        for force in (False, True):
            code, result = post({'type': 'detectors', 'name': 'torch_like_usad', 'force': force})
            assert code == 400 and '内置插件' in result['error']
            assert builtin.is_file()
        code, preview = post({'type': 'fault_types', 'name': 'uninstall_test_custom', 'force': False})
        assert code == 200 and preview['uninstalled'] is False and custom.exists()
        code, result = post({'type': 'fault_types', 'name': 'uninstall_test_custom', 'force': True})
        assert code == 200 and result['uninstalled'] is True and not custom.exists()
        assert builtin.is_file()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_frontend_handles_lost_reply_and_refuses_builtins():
    js = (ROOT / 'web' / 'visualization' / 'plugins.js').read_text(encoding='utf-8')
    assert 'item.builtin || !item.uninstallable' in js
    assert 'e.isNetworkFailure' in js
    assert 'await fetch(\'/api/plugins\'' in js
    assert '已不在目录中' in js
