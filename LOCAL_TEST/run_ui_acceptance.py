"""AAS V2 前端验收探针（自动部分）。

覆盖手册 S11《GUI 矩阵》中可以用机器判定的项目：页面可加载、静态资源可加载、
已下线入口不再出现、脚本语法正确、只读接口与已退役接口的契约保持。

无法自动判定的项（拖动框选手感、深色主题观感、导出文件可用性、分辨率适配等）
在输出里显式标记为 MANUAL，不冒充通过。

用法（在 AAS 根目录）：
    python LOCAL_TEST/run_ui_acceptance.py
    python LOCAL_TEST/run_ui_acceptance.py --json-out outputs/ui_acceptance/ui_acceptance.json
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web' / 'visualization'

PAGES = [
    'index.html', 'home.html', 'browser.html', 'simulation.html', 'preprocess.html',
    'runner.html', 'reports.html', 'plugins.html', 'monitor.html', 'import.html',
]

REF_PATTERN = re.compile(r'(?:src|href)=["\']([^"\']+)["\']')

API_CHECKS = [
    ('GET', '/api/auth/status', 200, '登录状态可查询'),
    ('GET', '/api/v2/telemetry/databases', 200, '活动数据库列表可查询'),
    ('GET', '/api/v2/telemetry/filter-options', 200, '筛选选项可查询'),
    ('GET', '/api/monitor/software', 200, '软件资源监控可查询'),
    ('GET', '/api/home/summary', 200, '首页汇总可查询'),
    ('GET', '/api/backup/list', 410, 'GUI 备份列表按契约退役'),
    ('POST', '/api/run-evaluation', 410, '旧实验入口按契约退役'),
    ('GET', '/api/simulation/tables', 410, '旧模拟库接口按契约退役'),
]

MANUAL_ITEMS = [
    '1366×768 与 1920×1080 下无横向滚动、无控件遮挡',
    '深色/浅色主题切换后，各模块无白底斑块、文字对比度可读',
    '历史数据库：滚轮缩放、拖动框选、双击复原的实际手感',
    '历史数据库：长参量列表滚动与故障查找响应',
    'CSV.GZ 导入弹窗：进度百分比推进、取消按钮可中断且库未写入',
    '训练/评估：进度条推进、完成后模型出现在模型列表',
    '任务查询与导出：导出的 CSV 能被 Excel 打开且行数一致',
    '预检失败、训练失败、导入失败时的错误提示可读且不阻断页面',
]


class NoRedirect(HTTPRedirectHandler):
    """不跟随 302，这样才能验证"备份页重定向"这个契约本身。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, timeout=20, method='GET', follow=True):
    data = b'{}' if method == 'POST' else None
    request = Request(url, data=data, method=method,
                      headers={'Content-Type': 'application/json'})
    opener = urlopen if follow else build_opener(NoRedirect).open
    try:
        with opener(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError as exc:
        return 0, str(exc).encode()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-out', type=Path,
                        default=ROOT / 'outputs' / 'ui_acceptance' / 'ui_acceptance.json')
    parser.add_argument('--database', default='aas_telemetry_v2.sqlite')
    args = parser.parse_args(argv)

    os.environ['AAS_V2_DB_FILENAME'] = args.database
    os.environ.setdefault('PYTHONUTF8', '1')
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from scripts import visualization_server as app  # noqa: E402  (env must be set first)
    from scripts.server.handlers import DashboardHandler  # noqa: E402

    class ProbeHandler(DashboardHandler):
        def authenticated_user(self, main_module):
            return 'ui-acceptance-probe'

    server = ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    results = []

    def add(item, expected, actual, detail=''):
        ok = expected == actual if not callable(expected) else bool(expected(actual))
        results.append({'item': item, 'expected': str(expected), 'actual': str(actual),
                        'status': 'PASS' if ok else 'FAIL', 'detail': detail})
        return ok

    try:
        # 1) 页面可加载
        page_bodies = {}
        for page in PAGES:
            code, body = fetch(base + '/' + page)
            page_bodies[page] = body.decode('utf-8', 'replace')
            add(f'页面可加载 /{page}', 200, code)

        # 2) 页面引用的本地资源与站内页面都可加载（.html 也要查，防止链接指向已删除的页面）
        for page, text in page_bodies.items():
            for ref in sorted(set(REF_PATTERN.findall(text))):
                if ref.startswith(('http://', 'https://', '#', 'mailto:', 'javascript:', 'data:', 'about:')):
                    continue
                target = ref.split('?')[0].split('#')[0]
                if not target:
                    continue
                code, _ = fetch(base + '/' + target)
                add(f'{page} 引用 {target}', 200, code)

        # 3) 已下线备份入口不再出现在任何页面
        for page, text in page_bodies.items():
            add(f'{page} 不含已下线备份链接', 0, text.count('backup.html'))

        # 4) 深色主题补丁可用且只由工作台挂载
        code, _ = fetch(base + '/theme-dark-overrides.css')
        add('深色覆盖补丁可加载', 200, code)
        shell = (WEB / 'workbench.js').read_text('utf-8')
        add('工作台挂载深色补丁', 1, shell.count('theme-dark-overrides.css'))
        add('工作台保留版式参数', 1, shell.count('match[4]'))

        # 5) 被页面引用的脚本通过 node --check
        node = subprocess.run(['node', '--version'], capture_output=True, text=True)
        if node.returncode == 0:
            scripts = set()
            for text in page_bodies.values():
                for ref in REF_PATTERN.findall(text):
                    if ref.split('?')[0].endswith('.js'):
                        scripts.add(ref.split('?')[0])
            for script in sorted(scripts):
                path = WEB / script
                if not path.is_file():
                    add(f'脚本存在 {script}', True, path.is_file())
                    continue
                check = subprocess.run(['node', '--check', str(path)],
                                       capture_output=True, text=True)
                add(f'脚本语法 {script}', 0, check.returncode, check.stderr.strip()[:200])
        else:
            results.append({'item': '脚本语法检查', 'expected': 'node 可用',
                            'actual': 'node 不可用', 'status': 'SKIP', 'detail': ''})

        # 6) 只读接口与已退役接口契约
        for method, path, expected, label in API_CHECKS:
            code, _ = fetch(base + path, method=method)
            add(f'{label} {method} {path}', expected, code)

        # 7) 备份页书签兼容：302 落到历史数据库，而不是 404
        code, _ = fetch(base + '/backup.html', follow=False)
        add('旧备份书签 302 兼容', 302, code)
    finally:
        server.shutdown()
        server.server_close()

    counts = {key: sum(1 for r in results if r['status'] == key) for key in ('PASS', 'FAIL', 'SKIP')}
    payload = {
        'generated_at': __import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds'),
        'database': args.database,
        'platform': sys.platform,
        'python': sys.version.split()[0],
        'automated': results,
        'manual_pending': MANUAL_ITEMS,
        'counts': counts,
        'verdict': 'PASS' if counts['FAIL'] == 0 else 'FAIL',
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    for row in results:
        if row['status'] != 'PASS':
            print(f"{row['status']}: {row['item']} (expected {row['expected']}, got {row['actual']}) {row['detail']}")
    print(f"automated PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']}")
    print(f"manual items pending: {len(MANUAL_ITEMS)}")
    print(f"report: {args.json_out}")
    return 0 if counts['FAIL'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
