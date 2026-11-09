"""Real HTTP authentication, dashboard counters and homepage access checks."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts import visualization_server
from scripts.server.home_auth import HomeAuth


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HomeLoginDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_auth = visualization_server.HOME_AUTH
        visualization_server.HOME_AUTH = HomeAuth(Path(cls.temp.name))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), visualization_server.DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.opener = build_opener(NoRedirect())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        visualization_server.HOME_AUTH = cls.old_auth
        cls.temp.cleanup()

    def call(self, path, body=None, cookie='', origin=None):
        headers = {'Cookie': cookie} if cookie else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if origin is not None:
            headers['Origin'] = origin
        req = Request(self.url + path, method='POST' if body is not None else 'GET',
                      data=json.dumps(body).encode('utf-8') if body is not None else None,
                      headers=headers)
        try:
            response = self.opener.open(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            payload = json.loads(data) if response.headers.get('Content-Type', '').startswith('application/json') else data
            return response.code, payload, response.headers

    def test_01_uninitialized_rejects_business_and_serves_home(self):
        status, data, _ = self.call('/api/auth/status')
        self.assertEqual((status, data['initialized'], data['authenticated']), (200, False, False))
        self.assertEqual(self.call('/api/databases')[0], 401)
        self.assertEqual(self.call('/api/home/summary')[0], 401)
        self.assertEqual(self.call('/api/models', {'unexpected':True})[0], 401)
        code, _, headers = self.call('/index.html')
        self.assertEqual((code, headers.get('Location')), (302, '/'))
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/home.js')[0], 200)

    def test_02_admin_bootstrap_login_dashboard_logout(self):
        self.assertEqual(self.call('/api/auth/setup', {'username':'admin', 'password':'short'})[0], 400)
        status, _, _ = self.call('/api/auth/setup', {'username':'admin', 'password':'long-password-2026'})
        self.assertEqual(status, 200)
        self.assertTrue((Path(self.temp.name) / 'configs' / '.aas_admin.json').is_file())
        self.assertEqual(self.call('/api/auth/setup', {'username':'admin', 'password':'long-password-2026'})[0], 400)
        self.assertEqual(self.call('/api/auth/login', {'username':'admin', 'password':'not-the-password'})[0], 401)
        status, payload, headers = self.call('/api/auth/login', {'username':'admin','password':'long-password-2026'})
        self.assertEqual(status, 200)
        self.assertNotIn('password',str(payload).lower())
        cookie=headers.get('Set-Cookie')
        self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=Strict',cookie)
        token=cookie.split(';')[0]
        self.assertTrue(self.call('/api/auth/status',cookie=token)[1]['authenticated'])
        status, data, _ = self.call('/api/home/summary',cookie=token)
        self.assertEqual(status, 200)
        self.assertIn('databases',data)
        self.assertIn('database_items',data)
        # databases = 已安装的规范遥测库数量（0/1）；database_items = 按分系统聚合的参量分布。
        # 两者口径不同，不能再用等号断言（旧实现把 database_items 当作库列表）。
        self.assertIn(data['databases'], (0, 1))
        for item in data['database_items']:
            self.assertLessEqual({'name', 'tables'}, set(item))
        self.assertGreaterEqual(data['tables'], 2)
        self.assertEqual(self.call('/index.html',cookie=token)[0], 200)
        # 旧物理表接口已退役，契约是 410（不再是 200）。
        self.assertEqual(self.call('/api/databases',cookie=token)[0], 410)
        self.assertEqual(self.call('/api/auth/logout',{},cookie=token)[0], 200)
        self.assertEqual(self.call('/api/home/summary',cookie=token)[0], 401)

    def test_03_cross_origin_blocked(self):
        self.assertEqual(self.call('/api/auth/login',{'username':'admin','password':'long-password-2026'},
                                   origin='https://another-host.invalid')[0], 403)

    def test_04_account_file_does_not_contain_plaintext_password(self):
        data=(Path(self.temp.name) / 'configs' / '.aas_admin.json').read_text(encoding='utf-8')
        self.assertNotIn('long-password-2026', data)
        self.assertIn('password_hash', data)

if __name__ == '__main__':
    unittest.main()
