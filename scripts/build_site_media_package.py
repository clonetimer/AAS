"""Refresh the V2 stable gate manifest and build the on-site delivery package.

The site package is the full source tree minus local data/environment:

* included: root docs/config, ``scripts``, ``tests``, ``web``, ``docs``,
  ``evaluator``, ``configs``, ``detectors``, ``core``, ``runner``, ``src``,
  ``utils``, ``native_plugins``, ``licenses``, ``LOCAL_TEST``, ``SITE_INSTALL``,
  ``datasets/README.md`` and ``datasets/templates/**``
* excluded: ``models``, ``outputs``, ``logs``, ``.venv``, caches, ``.git``,
  databases, compiled files, archives and ``configs/.aas_admin.json``

``DELIVERY_SHA256_MANIFEST.txt`` is regenerated from exactly those files and is
itself shipped inside the archive (a manifest cannot contain its own hash).

Run: python scripts/build_site_media_package.py gate
Run: python scripts/build_site_media_package.py pack
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
GATE = ROOT / 'tests' / 'v2_stable_gate.json'
MANIFEST = ROOT / 'DELIVERY_SHA256_MANIFEST.txt'
OUT = ROOT / 'outputs' / 'delivery' / 'AAS_V2_SiteFreshInstall_FeatureSchemaBind_Candidate_20261101.zip'
ASSET_SUFFIX = {'.html', '.css', '.js', '.json'}

ROOT_DIRS = {'scripts', 'tests', 'web', 'docs', 'evaluator', 'configs', 'detectors', 'core',
             'runner', 'src', 'utils', 'native_plugins', 'licenses', 'LOCAL_TEST', 'SITE_INSTALL'}
EXCLUDE_DIRS = {'models', 'outputs', 'logs', '.venv', '__pycache__', '.pytest_cache', '.git',
                'node_modules', '.idea', '.vscode', '.mypy_cache',
                '1dcnn_external_import_bundle'}
EXCLUDE_SUFFIX = {'.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.zip', '.log'}
EXCLUDE_NAME_SUFFIX = ('-wal', '-shm', '-journal')
EXCLUDE_NAMES = {'.aas_admin.json'}
ROOT_FILE_SUFFIX = {'.py', '.md', '.txt', '.yaml', '.yml', '.json'}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def refresh_gate() -> None:
    """Re-pin frontend asset hashes and register new gate tests."""
    manifest = json.loads(GATE.read_text(encoding='utf-8'))
    assets = {path.relative_to(ROOT).as_posix(): sha256(path)
              for path in sorted((ROOT / 'web' / 'visualization').rglob('*'))
              if path.is_file() and path.suffix in ASSET_SUFFIX}
    previous = manifest['frontend_sha256']
    drift = sorted(name for name in assets if previous.get(name) != assets[name])
    manifest['frontend_sha256'] = assets
    text = json.dumps(manifest, ensure_ascii=False, indent=2).replace('\n', '\r\n') + '\r\n'
    GATE.write_bytes(text.encode('utf-8'))
    print(f'gate: {len(assets)} frontend assets pinned, refreshed {drift}')


def register_gate_test(relative: str) -> None:
    """Add one test module to the stable gate; idempotent."""
    manifest = json.loads(GATE.read_text(encoding='utf-8'))
    if relative in manifest['test_files']:
        print(f'gate: {relative} already registered')
        return
    collected = subprocess.run([PYTHON, '-m', 'pytest', relative, '--collect-only', '-q',
                                '-p', 'no:cacheprovider'],
                               cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    if collected.returncode != 0:
        raise SystemExit('collect failed:\n' + collected.stdout + collected.stderr)
    nodes = [line.strip() for line in collected.stdout.splitlines()
             if line.strip().startswith('tests/') and '::' in line]
    if not nodes:
        raise SystemExit(f'no test nodes collected from {relative}')
    manifest['test_files'].append(relative)
    manifest['stable_nodes'].extend(nodes)
    text = json.dumps(manifest, ensure_ascii=False, indent=2).replace('\n', '\r\n') + '\r\n'
    GATE.write_bytes(text.encode('utf-8'))
    print(f'gate: registered {relative} with {len(nodes)} nodes')


def packaged_files() -> list[Path]:
    files: list[Path] = []
    for name in sorted(path.name for path in ROOT.iterdir()):
        path = ROOT / name
        if not path.is_file() or name in EXCLUDE_NAMES:
            continue
        if name == MANIFEST.name or name == OUT.name:
            continue
        if path.suffix in ROOT_FILE_SUFFIX or name == '.gitignore':
            files.append(path)
    for top in sorted(ROOT_DIRS):
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if not path.is_file() or path.name in EXCLUDE_NAMES:
                continue
            if path.suffix in EXCLUDE_SUFFIX or path.name.endswith(EXCLUDE_NAME_SUFFIX):
                continue
            files.append(path)
    readme = ROOT / 'datasets' / 'README.md'
    if readme.is_file():
        files.append(readme)
    templates = ROOT / 'datasets' / 'templates'
    if templates.is_dir():
        files.extend(sorted(path for path in templates.rglob('*') if path.is_file()))
    return files


def build() -> None:
    files = packaged_files()
    lines = [f'{sha256(path)}  AAS/{path.relative_to(ROOT).as_posix()}' for path in files]
    MANIFEST.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, Path('AAS') / path.relative_to(ROOT))
        archive.write(MANIFEST, Path('AAS') / MANIFEST.name)
    raw = sum(path.stat().st_size for path in files)
    print(f'manifest: {len(lines)} entries')
    print(f'zip: {OUT}')
    print(f'files={len(files) + 1}  raw={raw / 1048576:.2f} MB  zip={OUT.stat().st_size / 1048576:.2f} MB')
    print(f'zip sha256: {sha256(OUT)}')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command == 'gate':
        refresh_gate()
    elif command == 'register':
        if len(sys.argv) < 3:
            raise SystemExit('usage: build_site_media_package.py register <tests/file.py>')
        register_gate_test(sys.argv[2])
        refresh_gate()
    elif command == 'pack':
        build()
    else:
        raise SystemExit('usage: build_site_media_package.py gate|register <test>|pack')
