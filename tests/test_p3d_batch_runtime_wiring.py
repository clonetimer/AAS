from __future__ import annotations

import io
import json
from types import SimpleNamespace

from scripts.server import telemetry_v2_batch_tasks as batch
from scripts.server.handler_post import PostRoutesMixin


def test_batch_start_route_passes_bound_task_runtime_and_returns_accepted(
        tmp_path, monkeypatch):
    payload = {'task_id': 'a' * 32, 'confirm': True}
    observed = {}
    main = SimpleNamespace(
        ROOT=tmp_path,
        create_task=lambda *_args, **_kwargs: None,
        bind_task_thread=lambda *_args: None,
        update_task_progress=lambda *_args, **_kwargs: None,
        is_cancel_requested=lambda *_args: False,
        set_task_cancellable=lambda *_args, **_kwargs: True,
        complete_task=lambda *_args, **_kwargs: None,
        json_response=lambda _handler, body, status=200: {'http_status': status, **body},
    )

    def start(app_root, task_id, *, retry=False, task_runtime=None):
        observed.update(app_root=app_root, task_id=task_id, retry=retry,
                        task_runtime=task_runtime)
        return {'task_id': task_id, 'status': 'running'}

    monkeypatch.setattr(batch, 'start', start)

    class Handler(PostRoutesMixin):
        path = '/api/v2/telemetry/batch/start'
        headers = {'Content-Length': str(len(json.dumps(payload).encode('utf-8')))}
        rfile = io.BytesIO(json.dumps(payload).encode('utf-8'))
        server = SimpleNamespace(aas_application=main)

        def _handle_auth_post(self, _parsed, _main):
            return False

        def _require_auth(self, _parsed, _main, is_post=False):
            return True

    response = Handler()._dispatch_POST()

    assert observed['task_runtime'] is main
    assert observed['task_id'] == payload['task_id']
    assert observed['retry'] is False
    assert response['http_status'] == 202
