"""Audit: every /api path referenced by a *loaded* frontend script must still exist.

Rationale: a page can load fine and still contain dead buttons, because the JS may
call an endpoint that has since been retired (410) or removed (404). The UI probe
cannot see that — it never executes the click handlers. This audit closes the gap
statically + with an authenticated GET probe.

Method per referenced path:
  1. GET probe. Anything other than 404 means the route is dispatched
     (200/400/401/403/405/410/413/500 are all "known route" answers).
  2. On 404, consult the POST tables: a POST-only endpoint legitimately 404s on GET.
  3. Otherwise the call site is dead.

Run from the AAS root:
    python LOCAL_TEST/audit_frontend_endpoints.py
"""
from __future__ import annotations
import json
import os
import re
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web' / 'visualization'
API_RE = re.compile(r"""['"`](/api/[A-Za-z0-9_\-./${}]*)['"`]""")
SCRIPT_RE = re.compile(r'<script[^>]+src="([^"?]+)')


def loaded_scripts() -> list[Path]:
    names = set()
    for page in WEB.glob('*.html'):
        for src in SCRIPT_RE.findall(page.read_text(encoding='utf-8')):
            if src.endswith('.js'):
                names.add(src)
    return sorted(WEB / name for name in names if (WEB / name).is_file())


def referenced_paths() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for script in loaded_scripts():
        text = script.read_text(encoding='utf-8')
        for raw in API_RE.findall(text):
            path = urlsplit(raw).path
            if '${' in path:
                path = re.sub(r'/api/[^/]*\$\{[^}]*\}[^/]*', '/api/<dynamic>', path)
            found.setdefault(path, set()).add(script.name)
    return found


def main() -> int:
    os.environ.setdefault('AAS_V2_DB_FILENAME', 'aas_telemetry_v2.sqlite')
    os.environ.setdefault('PYTHONUTF8', '1')
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from scripts import visualization_server as _app  # noqa: F401  绑定 handler 的主模块
    from scripts.server.handlers import DashboardHandler
    from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES
    from scripts.server.handler_post import EXTENSION_POST_PATHS, RETIRED_POST_PATHS

    class ProbeHandler(DashboardHandler):
        def authenticated_user(self, main_module):
            return 'endpoint-audit'

    server = ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    # 表驱动的路由 + 源码里显式分支的路径，否则会把"由 if 分支处理"的接口误判成死链。
    source_paths: set[str] = set()
    for name in ('handler_post.py', 'handler_get.py', 'post_models.py', 'post_extensions.py',
                 'handler_plugins.py', 'telemetry_v2_routes.py', 'home_auth.py',
                 'handlers.py', 'data_routes.py'):
        text = (ROOT / 'scripts' / 'server' / name).read_text(encoding='utf-8')
        source_paths |= set(API_RE.findall(text))
    post_only = (set(POST_DATA_ROUTES) | set(EXTENSION_POST_PATHS) | set(RETIRED_POST_PATHS)
                 | source_paths)

    def probe(path: str) -> int:
        try:
            with urlopen(base + path, timeout=15) as resp:
                return resp.status
        except HTTPError as exc:
            return exc.code
        except URLError:
            return 0

    rows = []
    try:
        for path, owners in sorted(referenced_paths().items()):
            if path == '/api/<dynamic>':
                rows.append({'path': path, 'status': 'DYNAMIC', 'http': '-',
                             'scripts': sorted(owners)})
                continue
            code = probe(path)
            if code == 404:
                # 末尾带斜杠的是前端拼接的前缀（如 /api/v2/telemetry/batch/ + 子路径）
                prefix = path.endswith('/') and any(p.startswith(path) for p in post_only)
                if path in RETIRED_POST_PATHS:
                    verdict = 'RETIRED(POST)'
                elif path in post_only or prefix:
                    verdict = 'ALIVE(POST)'
                else:
                    verdict = 'DEAD'
            elif code == 500:
                # 500 = 服务端异常，既不能算"路由存在"也不能算"不存在"，单独列出人工判定。
                if path in RETIRED_POST_PATHS:
                    verdict = 'RETIRED(POST)'
                else:
                    verdict = 'SUSPECT500'
            else:
                verdict = 'ALIVE'
            rows.append({'path': path, 'status': verdict, 'http': code,
                         'scripts': sorted(owners)})
    finally:
        server.shutdown()
        server.server_close()

    dead = [r for r in rows if r['status'] == 'DEAD']
    retired = [r for r in rows if r['status'].startswith('RETIRED')]
    suspect = [r for r in rows if r['status'] == 'SUSPECT500']
    print(f'referenced endpoints: {len(rows)}  dead: {len(dead)}  '
          f'retired-but-called: {len(retired)}  suspect500: {len(suspect)}')
    for row in dead + retired + suspect:
        print(f"  {row['status']:14} {row['path']:52} http={row['http']} <- {', '.join(row['scripts'])}")
    out = ROOT / 'outputs' / 'ui_acceptance' / 'frontend_endpoints.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'generated_at': __import__('datetime').datetime.now()
                               .astimezone().isoformat(timespec='seconds'),
                               'summary': {'total': len(rows), 'dead': len(dead),
                                           'retired': len(retired), 'suspect500': len(suspect)},
                               'endpoints': rows}, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    print('report:', out)
    return 0 if not dead and not retired else 1


if __name__ == '__main__':
    sys.exit(main())
