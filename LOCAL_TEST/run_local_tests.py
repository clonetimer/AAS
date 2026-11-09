"""Safe, evidence-producing Phase 53 local test launcher (Windows / POSIX).

Examples from the AAS root:
  python LOCAL_TEST/run_local_tests.py --mode smoke
  python LOCAL_TEST/run_local_tests.py --mode full
  python LOCAL_TEST/run_local_tests.py --mode migration --source-db D:/data/old.sqlite \
      --review-db D:/data/old_review.sqlite --gate-report outputs/.../v2_gate.json

Never modifies or replaces the source SQLite database.  Does not automatically
switch the application's active database, migrate models, or sign Stable.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
FOCUSED = [
    'tests/test_v2_source_file_import_phase33.py',
    'tests/test_v2_training_evaluation_regression_20261020.py',
    'tests/test_phase53_r6_windows_portability.py',
    'tests/test_phase35_modular_boundaries.py',
    'tests/test_phase36_preprocessing_algorithms.py',
    'tests/test_phase53_r7_windows_portability.py',
    'tests/test_phase47_import_metadata_wizard.py',
    'tests/test_phase48_batch_streaming.py',
    'tests/test_phase49_batch_ui.py',
    'tests/test_phase51_raw_row_storage.py',
    'tests/test_phase52_compaction_acceptance.py',
    'tests/test_phase53_release_acceptance.py',
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for data in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('smoke', 'full', 'migration'), default='smoke')
    p.add_argument('--report-dir', type=Path, help='New, empty report folder; default is a timestamped outputs folder')
    p.add_argument('--source-db', type=Path, help='Read-only, stopped-service V2 database for migration')
    p.add_argument('--review-db', type=Path, help='New destination SQLite file; must NOT exist')
    p.add_argument('--gate-report', type=Path, help='Full PASS gate report produced by this Windows installation')
    p.add_argument('--production', action='store_true', help='Flag report provenance only; never grants Stable sign-off')
    p.add_argument('--batch-timeout-seconds', type=int, default=300)
    args = p.parse_args(argv)
    if args.mode == 'migration':
        if not args.source_db or not args.review_db or not args.gate_report:
            p.error('migration requires --source-db, --review-db, and --gate-report')
        source = args.source_db.resolve()
        review = args.review_db.resolve()
        gate = args.gate_report.resolve()
        if source == review:
            p.error('refusing to overwrite source database')
        if not source.is_file() or review.exists() or not gate.is_file():
            p.error('source DB and gate report must exist, destination DB must not exist')
        try:
            with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True)) as con:
                if con.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone() != ('2',):
                    p.error('source is not a valid AAS V2 database')
        except sqlite3.Error as e:
            p.error(f'cannot read source V2 database: {e}')
    else:
        source = review = gate = None
    if not 30 <= args.batch_timeout_seconds <= 3600:
        p.error('--batch-timeout-seconds must be 30..3600')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out = (args.report_dir or (ROOT / 'outputs' / 'phase53_windows_tests' / stamp)).resolve()
    if out.exists() and any(out.iterdir()):
        p.error('report directory already contains files; choose a fresh folder')
    out.mkdir(parents=True, exist_ok=True)
    record = {'mode': args.mode, 'platform': sys.platform, 'python': sys.version,
              'created_utc': now(), 'source_db': str(source) if source else None,
              'review_db': str(review) if review else None, 'steps': [],
              'status': 'running', 'report_dir': str(out)}
    summary = out / 'local_test_summary.json'

    def save():
        tmp = summary.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, summary)

    def step(name: str, argv_: list[str], timeout: int | None = None):
        dest = out / f'{len(record["steps"])+1:02d}_{name}.log'
        print(f'[{now()}] START {name}', flush=True)
        started = time.monotonic()
        status = 'failed'; code = None
        with dest.open('w', encoding='utf-8', errors='replace') as log:
            try:
                # Windows legacy locales (notably cp936/GBK) must not leak into
                # any pytest, gate worker, or migration subprocess. The project
                # stores source assets and evidence as UTF-8. Subprocesses of the
                # gate inherit this setting; do not change the user's global env.
                child_env = os.environ.copy()
                child_env['PYTHONUTF8'] = '1'
                completed = subprocess.run([sys.executable, *argv_], cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT,
                                            env=child_env, timeout=timeout, check=False)
                code = completed.returncode
                status = 'passed' if code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                status = 'timeout'; code = 124
                log.write('\nERROR: command exceeded the configured deadline.\n')
            except Exception:
                log.write(traceback.format_exc()); code = 2
        result = {'name': name, 'status': status, 'exit_code': code,
                  'duration_s': round(time.monotonic()-started, 3), 'log': dest.name}
        record['steps'].append(result)
        save()
        print(f'[{now()}] {status.upper()} {name} (exit {code}); log: {dest}', flush=True)
        if status != 'passed':
            raise RuntimeError(f'{name} did not pass; inspect {dest}')

    try:
        save()
        step('original_source_sha256', ['LOCAL_TEST/verify_delivery.py'], timeout=120)
        step('fixed_manifest_and_frontend', ['scripts/check_v2_stable.py', '--verify-only'], timeout=240)
        if args.mode == 'smoke':
            step('import_migration_release_smoke', ['-m','pytest','-q',*FOCUSED], timeout=900)
        elif args.mode == 'full':
            full = out / 'v2_gate.json'
            # Gate itself bounds every subprocess; outer bound is intentionally generous.
            step('full_v2_gate', ['scripts/check_v2_stable.py',
                 '--batch-timeout-seconds', str(args.batch_timeout_seconds),
                 '--report-json', str(full)], timeout=args.batch_timeout_seconds * 50 + 600)
            loaded = json.loads(full.read_text(encoding='utf-8'))
            if loaded.get('status') != 'passed' or any(x.get('exit_code') != 0 for x in loaded.get('batches', [])):
                raise RuntimeError('gate report missing complete successful batch execution')
            record['gate_report'] = str(full)
        else:
            # Never modify source, even if this caller chooses --production.
            record['source_db_sha256_before'] = sha256(source)
            record['source_db_bytes_before'] = source.stat().st_size
            save()
            step('migration_readonly_preview', ['-m','scripts.compact_v2_raw_rows',
                 '--source',str(source),'--dry-run'], timeout=1800)
            step('migration_new_copy', ['-m','scripts.compact_v2_raw_rows',
                 '--source',str(source),'--output',str(review)], timeout=None)
            step('migration_full_row_verification', ['-m','scripts.verify_v2_compaction',
                 '--source',str(source),'--output',str(review),
                 '--report',str(out / 'sample_verification.json')], timeout=None)
            cmd = ['-m','scripts.phase53_release_acceptance',
                   '--source',str(source),'--output',str(review),
                   '--gate-report',str(gate),'--report',str(out / 'release_acceptance.json')]
            if args.production:
                cmd.append('--production')
            step('migration_business_and_release_acceptance',cmd,timeout=None)
            record['source_db_sha256_after'] = sha256(source)
            record['source_db_bytes_after'] = source.stat().st_size
            if record['source_db_sha256_before'] != record['source_db_sha256_after']:
                raise RuntimeError('source database file changed during test; do not switch')
            record['review_db_bytes'] = review.stat().st_size
            record['release_status'] = 'Candidate; requires manual Windows production sign-off'
        record['status'] = 'passed'
        record['finished_utc'] = now()
        save()
        print(f'PASS: {args.mode} completed; evidence: {summary}\n'
              'NOTICE: This is Candidate evidence, not a Stable release authorization.', flush=True)
        return 0
    except BaseException as e:
        record['status'] = 'failed'
        record['finished_utc'] = now()
        record['error'] = str(e)
        save()
        print(f'FAIL: {e}\nEvidence retained in {out}', file=sys.stderr, flush=True)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
