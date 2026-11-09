"""Live HTTP + jsdom CSV wizard acceptance; never uses the installed user's data.

Development setup: install the normal Python runtime dependencies, Node >=18,
then run `npm ci` at the repository root (pinned jsdom 26.1.0).
Run: python LOCAL_TEST/run_csv_wizard_acceptance.py
Optional: --json-out PATH. Node/jsdom are test dependencies, not AAS runtime needs.
The server and Node test are child processes of this command so they share its
network namespace. This checks real HTTP/DOM behavior, not visual/browser QA.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    bodies = {
        'history': 'time_s,eps.battery_energy_j,label\n' + ''.join(f'{i},{1000+i},{i%2}\n' for i in range(8)),
        'simulation': 'frame,time_s,voltage,label,note\n' + ''.join(
            f'{i+1},{100+i},{20+i/10},{i%2},"完整,第{i+1}帧"\n' for i in range(8)),
        'train': 'time_s,speed\n' + ''.join(f'{200+i},{50+i}\n' for i in range(8)),
        'eval': 'time_s,speed\n' + ''.join(f'{400+i},{150+i}\n' for i in range(8)),
    }
    return {key: {'name': f'acceptance_{key}.csv', 'text': text} for key, text in bodies.items()}


def copy_application(target):
    def excluded(directory, names):
        return [name for name in names if name.startswith('.') or name in
                {'node_modules', '__pycache__', 'outputs', 'logs', 'venv', 'environment'} or
                (Path(directory) / name).is_symlink() or name.lower().endswith(
                    ('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm', '.sqlite-wal',
                     '.sqlite-shm', '.pyc', '.pkl', '.pickle', '.joblib', '.pt', '.pth', '.onnx', '.zip'))]
    target.mkdir()
    for name in ('configs', 'core', 'detectors', 'evaluator', 'runner', 'scripts', 'src', 'utils', 'web', 'native_plugins'):
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, target / name, ignore=excluded)
    (target / 'datasets').mkdir()


def verify_databases(app, inputs):
    with closing(sqlite3.connect(app / 'datasets/aas_telemetry_v2.sqlite')) as db:
        sources = db.execute('SELECT id,source_name,source_sha256,source_bytes FROM v2_sources ORDER BY id').fetchall()
        assert len(sources) == 3, f'Expected history/train/eval sources only: {sources}'
        checked = []
        for purpose, expected in [('history', [0, 1]*4), ('train', [0]*8), ('eval', [None]*8)]:
            fixture = inputs[purpose]
            row = next(item for item in sources if item[1] == fixture['name'])
            raw = fixture['text'].encode('utf-8')
            assert row[2:] == (hashlib.sha256(raw).hexdigest(), len(raw)), purpose
            samples = db.execute('''SELECT g.role,s.label_int FROM v2_samples s
                JOIN v2_segments g ON g.id=s.segment_id WHERE g.source_id=? ORDER BY s.source_ordinal''', (row[0],)).fetchall()
            assert [item[1] for item in samples] == expected, (purpose, samples)
            assert {item[0] for item in samples} == {'train' if purpose == 'train' else 'test'}
            checked.append({'purpose': purpose, 'source_id': row[0], 'labels': expected, 'sha256': row[2]})
    with closing(sqlite3.connect(app / 'datasets/aas_simulation_v2.sqlite')) as db:
        rows = db.execute('SELECT id,parent_id,version,filename,events_json,root_sha256 FROM v2_csv_datasets').fetchall()
        assert len(rows) == 1 and rows[0][1:5] == (None, 0, inputs['simulation']['name'], '[]'), rows
        assert rows[0][5] == hashlib.sha256(inputs['simulation']['text'].encode('utf-8')).hexdigest()
        saved = [json.loads(row[0]) for row in db.execute('SELECT row_json FROM v2_csv_frames ORDER BY frame')]
        assert saved == list(csv.DictReader(io.StringIO(inputs['simulation']['text']))), saved
    tasks = [json.loads(path.read_text(encoding='utf-8')) for path in (app / 'outputs/task_state').glob('*.json')]
    assert len(tasks) == 9 and all(t['task_type'] == 'csv_import' and t['status'] == 'completed' for t in tasks), tasks
    return {'sources': checked, 'simulation_full_rows': len(saved), 'simulation_versions': 1,
            'completed_csv_tasks': len(tasks), 'automatic_algorithm_or_fault_tasks': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-out', type=Path, default=ROOT / 'outputs/csv_wizard_acceptance/report.json')
    args = parser.parse_args()
    report = {'status': 'FAIL', 'scope': 'live HTTP + jsdom DOM; visual and Windows GUI acceptance remain separate',
              'platform': sys.platform, 'python': sys.version.split()[0],
              'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    with tempfile.TemporaryDirectory(prefix='aas-csv-wizard-') as temporary:
        temp = Path(temporary); app = temp / 'app'; server = None
        try:
            copy_application(app)
            report['tested_files'] = {name: hashlib.sha256((app / 'web/visualization' / name).read_bytes()).hexdigest()
                                      for name in ('import.html', 'simulation.html', 'runner.html', 'csv-import-wizard.js',
                                                   'telemetry-v2-import.js', 'simulation-csv-workspace.js', 'runner-csv-import.js')}
            inputs = fixtures(); fixture_file = temp / 'fixtures.json'; node_report = temp / 'dom.json'
            fixture_file.write_text(json.dumps(inputs, ensure_ascii=False), encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if not k.startswith('AAS_') and k != 'PYTHONPATH'}
            env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1')
            subprocess.run([sys.executable, '-c', "import sqlite3; from scripts.rebuild_telemetry_v2 import create_v2; c=sqlite3.connect('datasets/aas_telemetry_v2.sqlite'); create_v2(c); c.commit(); c.close()"], cwd=app, env=env, check=True)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            with (temp / 'server.log').open('w', encoding='utf-8') as log:
                server = subprocess.Popen([sys.executable, 'scripts/visualization_server.py', '--host', '127.0.0.1', '--port', str(port)], cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 45
                while True:
                    try:
                        with urlopen(base + '/api/auth/status', timeout=1) as response:
                            assert json.load(response)['initialized'] is False
                        break
                    except OSError:
                        if server.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Disposable server did not become ready')
                        time.sleep(0.1)
                run = subprocess.run(['node', str(ROOT / 'tests/csv_wizard_live_acceptance.cjs'), base, str(fixture_file), str(node_report)], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=150)
                report['dom'] = json.loads(node_report.read_text(encoding='utf-8')) if node_report.exists() else {}
                if run.returncode:
                    raise RuntimeError((run.stderr or run.stdout)[-4000:])
                report['database_checks'] = verify_databases(app, inputs)
                report['status'] = 'PASS'
        except Exception as error:
            report['error'] = str(error)
            log_path = temp / 'server.log'
            report['server_log_tail'] = log_path.read_text(encoding='utf-8')[-6000:] if log_path.exists() else ''
        finally:
            if server and server.poll() is None:
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"CSV wizard live acceptance: {report['status']} -> {args.json_out}")
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
