"""HTTP handler module for the visualization dashboard.

This module contains the DashboardHandler class which was extracted from
visualization_server.py to improve code organization and maintainability.

All global functions (json_response, list_databases, run_evaluation, etc.)
remain in visualization_server.py and are accessed via the _get_main_module()
helper for backward compatibility.
"""
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import sys
import threading
import uuid
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES
from scripts.server.v2_database_config import active_name, active_path
from scripts.server.home_auth import AuthError, auth_cookie, expired_cookie, same_origin
from scripts.server.model_source_contract import model_input_sources
from scripts.server.result_traces import chart_trace
from scripts.server.execution_provenance import data_fingerprint
from scripts.server.event_scoring import enrich_trace, load_events, source_tables as event_source_tables



from .handler_runtime import resolve_application as _get_main_module, main_from_compatibility_facade
from .handler_plugins import (PLUGIN_INTEGRATION, collect_plugins, install_plugin, preview_plugin, uninstall_plugin)
from .handler_get import GetRoutesMixin
from .handler_post import PostRoutesMixin

class DashboardHandler(GetRoutesMixin, PostRoutesMixin, SimpleHTTPRequestHandler):
    """可视化面板的 HTTP 请求处理器。

    处理所有前端 API 请求，包括：
    - 数据库/表/实验/模型的 CRUD 操作
    - 模型训练和评估
    - 数据预处理
    - 仿真数据管理
    - 算法导入和管理
    - 系统监控和备份
    """

    def __init__(self, *args, **kwargs):
        server = args[2] if len(args) >= 3 else kwargs.get('server')
        main = getattr(server, 'aas_application', None) or _get_main_module()
        static_dir = main.STATIC_DIR if main else None
        if static_dir:
            super().__init__(*args, directory=str(static_dir), **kwargs)
        else:
            super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        """Best-effort HTTP log: an invalid Windows console handle is not an API error.

        pytest, pythonw and detached Windows processes can leave the original
        sys.__stdout__ pointing at an invalid OS handle. BaseHTTPRequestHandler
        invokes log_message *inside send_response*, so a failed log must never
        abort an otherwise successful JSON/401 response.
        """
        sink = sys.stderr
        if sink is None:
            return
        try:
            print(f"[HTTP] {self.client_address[0]} - {format % args}",
                  file=sink, flush=True)
        except (OSError, ValueError, AttributeError):
            # Logging is diagnostic only. This must never change HTTP status
            # or truncate the response when the stream is closed/invalid.
            pass

    def end_headers(self):
        """Always revalidate frontend assets after replacing the installed package."""
        static_path = urlparse(self.path).path.lower()
        if static_path.endswith(('.html', '.js', '.css')) or static_path == '/':
            self.send_header('Cache-Control', 'no-store, max-age=0')
        super().end_headers()

    def authenticated_user(self, main):
        """Return logged-in identity, or None (override only in isolated tests)."""
        return main.HOME_AUTH.user(self.headers.get('Cookie', ''))

    def _send_auth_response(self, payload, status=200, cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def _handle_auth_post(self, parsed, main):
        """Return True if the authentication request was handled."""
        if parsed.path not in ('/api/auth/setup', '/api/auth/login', '/api/auth/logout'):
            return False
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096:
                raise AuthError('请求体大小不符合要求')
            if not same_origin(self.headers.get('Origin', ''), self.headers.get('Host', '')):
                self._send_auth_response({'error': '跨站请求被拒绝'}, 403)
                return True
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise AuthError('请求必须使用 JSON')
            payload = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(payload, dict):
                raise AuthError('请求格式无效')
            if parsed.path == '/api/auth/setup':
                main.HOME_AUTH.setup(payload.get('username'), payload.get('password'), self.client_address[0])
                self._send_auth_response({'ok': True})
            elif parsed.path == '/api/auth/login':
                token = main.HOME_AUTH.login(payload.get('username', ''), payload.get('password', ''),
                                              self.client_address[0])
                self._send_auth_response({'ok': True, 'username': payload['username']},
                                         cookie=auth_cookie(token))
            else:
                main.HOME_AUTH.logout(self.headers.get('Cookie', ''))
                self._send_auth_response({'ok': True}, cookie=expired_cookie())
        except (AuthError, ValueError, TypeError, json.JSONDecodeError) as exc:
            code = 401 if parsed.path == '/api/auth/login' else 400
            self._send_auth_response({'error': str(exc)}, code)
        return True

    def _discard_rejected_write_body(self):
        """Drain a small rejected request before closing the HTTP/1.0 socket.

        On Windows, closing a socket with unread POST bytes can abort the TCP
        connection after the status line but before the JSON error body arrives.
        Only bounded, length-delimited bodies are drained; untrusted clients
        cannot make authentication wait indefinitely for a large upload.
        """
        if self.headers.get('Transfer-Encoding'):
            return
        try:
            remaining = int(self.headers.get('Content-Length', '0'))
        except (TypeError, ValueError):
            return
        if not 0 < remaining <= 65536:
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(1.0)
            while remaining:
                block = self.rfile.read(min(remaining, 8192))
                if not block:
                    break
                remaining -= len(block)
        except (OSError, ValueError):
            # A truncated/malicious request must not bypass authentication.
            pass
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass

    def _require_auth(self, parsed, main, *, is_post=False):
        """Serve 401 for APIs, redirect protected HTML back to the home page."""
        if is_post and not same_origin(self.headers.get('Origin', ''), self.headers.get('Host', '')):
            self._discard_rejected_write_body()
            self._send_auth_response({'error': '跨站请求被拒绝'}, 403)
            return False
        if self.authenticated_user(main):
            return True
        if parsed.path.startswith('/api/') or is_post:
            if is_post:
                self._discard_rejected_write_body()
            self._send_auth_response({'error': '需要登录', 'code': 'AUTH_REQUIRED'}, 401)
        else:
            self.send_response(302)
            self.send_header('Location', '/')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
        return False

    def send_response(self, code, message=None):
        self._aas_response_code = int(code)
        return super().send_response(code, message)

    def _measured_request(self, method, dispatch):
        import time as _time
        started = _time.perf_counter()
        self._aas_response_code = 500
        try:
            return dispatch()
        finally:
            from utils.monitor import monitor
            monitor.record_api_call(self.path, (_time.perf_counter() - started) * 1000,
                                    status=self._aas_response_code, method=method)

    def do_GET(self):
        return self._measured_request('GET', self._dispatch_GET)

    def do_POST(self):
        return self._measured_request('POST', self._dispatch_POST)

    def do_DELETE(self):
        """处理 DELETE 请求。"""
        parsed = urlparse(self.path)
        main = main_from_compatibility_facade(self)
        if main is None:
            self.send_error(500, 'Application context is not available')
            return
        if not self._require_auth(parsed, main, is_post=True):
            return

        if parsed.path.startswith("/api/models/"):
            try:
                model_id = int(parsed.path.split("/api/models/")[1])
                result = main.delete_model(model_id)
                status_code = 200 if result["status"] == "success" else 404
                return main.json_response(self, result, status_code)
            except (ValueError, IndexError):
                return main.json_response(self, {"error": "无效的模型ID"}, 400)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
        return main.json_response(self, {"error": "not found"}, 404)

    def do_PUT(self):
        """处理 PUT 请求。"""
        parsed = urlparse(self.path)
        main = main_from_compatibility_facade(self)
        if main is None:
            self.send_error(500, 'Application context is not available')
            return
        if not self._require_auth(parsed, main, is_post=True):
            return

        if parsed.path.startswith("/api/models/") and parsed.path.endswith("/status"):
            try:
                raw_id = parsed.path.split("/api/models/")[1].split("/status")[0]
                model_id = int(raw_id)
            except (ValueError, IndexError):
                return main.json_response(self, {"error": "无效的模型ID"}, 400)
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)
                result = main.update_model_status(model_id, data.get("status", ""))
                status_code = 200 if result.get("status") == "success" else 404
                return main.json_response(self, result, status_code)
            except (ValueError, json.JSONDecodeError) as exc:
                return main.json_response(self, {"error": str(exc)}, 400)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 500)
        return main.json_response(self, {"error": "not found"}, 404)
