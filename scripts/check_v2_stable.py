#!/usr/bin/env python3
"""Run an auditable V2 stable test gate (no blanket pytest -k exclusions).

The manifest freezes exact node IDs. Newly added/removed tests cause a gate
error until the manifest is reviewed. Dataset-dependent cases are explicitly
listed in an external-data tier; they are never counted as passed in CI.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import signal
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tests' / 'v2_stable_gate.json'


def _run(args: list[str], *, capture=False, timeout=180):
    """Bound the entire pytest process group; never hang indefinitely at exit.

    An emitted pytest success banner is not evidence of a passing gate until
    the process has actually exited with status zero. Windows and POSIX have
    different child-process cleanup semantics.
    """
    command=[sys.executable, '-m', 'pytest', *args]
    # Windows legacy locales (notably cp936/GBK) must not leak into the isolated
    # pytest worker processes: the source assets and evidence are UTF-8, and the
    # HTTP handlers/logging carry Chinese text. Propagate an explicit UTF-8 mode
    # so the gate behaves like the Linux baseline regardless of host codepage.
    child_env = os.environ.copy()
    child_env['PYTHONUTF8'] = '1'
    kw={'cwd':ROOT, 'text':True, 'env':child_env,
        'stdout':subprocess.PIPE if capture else None,
        'stderr':subprocess.PIPE if capture else None}
    if os.name == 'nt':
        kw['creationflags']=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    else:
        kw['start_new_session']=True
    process=subprocess.Popen(command,**kw)
    try:
        stdout,stderr=process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
    except subprocess.TimeoutExpired:
        if os.name=='nt':
            subprocess.run(['taskkill','/F','/T','/PID',str(process.pid)],
                           capture_output=True,timeout=15,check=False)
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        stdout,stderr=process.communicate(timeout=10)
        print(f'FAIL: pytest did not terminate in {timeout}s; process tree killed. '
              'The gate remains FAILED despite any previous success banner.',file=sys.stderr,flush=True)
        return subprocess.CompletedProcess(command,124,stdout,stderr)


def _collect(paths: list[str]) -> set[str]:
    result = _run(['--collect-only', '-q', *paths], capture=True)
    if result.returncode != 0:
        raise RuntimeError(f'Unable to collect V2 stable suite:\n{result.stdout}\n{result.stderr}')
    return {line.strip() for line in result.stdout.splitlines()
            if line.strip().startswith('tests/') and '::' in line}


def _partition_tests(stable: list[str], max_tests: int) -> list[list[str]]:
    """Bound cross-test state and runtime without omitting or reordering nodes.

    Algorithm worker-process tests are always isolated by module. Other
    suites are packed by contiguous modules up to max_tests test nodes.
    """
    if not isinstance(max_tests,int) or max_tests<1:
        raise ValueError('max_tests must be positive')
    batches: list[list[str]] = []
    current: list[str] = []
    current_isolation = 'normal'
    isolated = (
        'tests/test_phase39_model_storage_contract.py::',
        'tests/test_phase40_training_reliability.py::',
        'tests/test_phase41_algorithm_process.py::',
    )
    # Some model/worker tests register shutdown hooks and interact with native
    # libraries. Narrow their subprocess lifetime even when max_tests is high;
    # a completed pytest banner alone is not evidence that teardown exited.
    isolation_limit = {isolated[0]:1, isolated[1]:1, isolated[2]:1}
    for node in stable:
        # Model registration, training journals and algorithm workers carry
        # process-level teardown state. Never mix these modules with each
        # other or with ordinary HTTP test modules in a pytest process.
        isolation = next((name for name in isolated if node.startswith(name)), 'normal')
        limit = min(max_tests, isolation_limit.get(isolation,max_tests))
        if current and (len(current)>=limit or isolation!=current_isolation):
            batches.append(current);current=[]
        current.append(node)
        current_isolation=isolation
    if current: batches.append(current)
    assert [node for batch in batches for node in batch]==stable
    return batches


def _write_gate_report(path: Path, report: dict) -> None:
    """Durable report, including incomplete/failing batches; never success-only."""
    path.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
        f.flush()
        os.fsync(f.fileno())
        temp=Path(f.name)
    os.replace(temp,path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-external-data', action='store_true',
                        help='Also run the separately listed preinstalled-dataset checks')
    parser.add_argument('--batch-timeout-seconds',type=int,default=180,
                        help='Hard deadline for each isolated pytest process (default 180s)')
    parser.add_argument('--max-batch-tests',type=int,default=20,
                        help='Maximum test nodes in each isolated pytest process (default 20)')
    parser.add_argument('--report-json',type=Path,
                        help='Write machine-readable per-batch test and exit status')
    parser.add_argument('--verify-only', action='store_true',
                        help='Check manifest / static integrity without running pytest')
    args = parser.parse_args()
    if not 30 <= args.batch_timeout_seconds <= 3600:
        parser.error('batch-timeout-seconds must be 30..3600')
    if not 1 <= args.max_batch_tests <= 100:
        parser.error('max-batch-tests must be 1..100')
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    stable = manifest['stable_nodes']
    external = manifest['external_data_nodes']
    discovered = _collect(manifest['test_files'])
    expected = set(stable) | set(external)
    if len(stable) != len(set(stable)) or len(external) != len(set(external)):
        print('FAIL: duplicate node ids in manifest', file=sys.stderr)
        return 2
    if discovered != expected:
        print('FAIL: V2 test manifest drift. Review newly added/removed tests.', file=sys.stderr)
        print('New: ' + repr(sorted(discovered-expected)), file=sys.stderr)
        print('Missing: ' + repr(sorted(expected-discovered)), file=sys.stderr)
        return 2
    expected_assets = manifest['frontend_sha256']
    drift = []
    for filename, wanted in expected_assets.items():
        target = ROOT / filename
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else 'MISSING'
        if actual != wanted:
            drift.append(filename)
    actual_assets = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'web' / 'visualization').rglob('*')
                     if p.is_file() and p.suffix in {'.html', '.css', '.js', '.json'}}
    if drift or actual_assets != set(expected_assets):
        print('FAIL: frontend assets changed without explicit review:',
              sorted(set(drift) | (actual_assets ^ set(expected_assets))), file=sys.stderr)
        return 2
    print(f'V2 gate manifest checked: {len(stable)} stable tests, '
          f'{len(external)} external-data tests, {len(expected_assets)} frontend assets.', flush=True)
    if args.verify_only:
        return 0
    # Each test node runs exactly once. Small isolated processes prevent test
    # workers/servers from contaminating later modules, while per-batch
    # deadlines distinguish a failed gate from a completed pytest banner.
    batches=_partition_tests(stable,args.max_batch_tests)
    if args.with_external_data:
        batches.extend(_partition_tests(external,args.max_batch_tests))
    report={'schema_version':1,'status':'running',
            'started_utc':datetime.now(timezone.utc).isoformat(),
            'stable_node_count':len(stable),'external_node_count':len(external) if args.with_external_data else 0,
            'max_batch_tests':args.max_batch_tests,'batch_timeout_seconds':args.batch_timeout_seconds,
            'batches':[]}
    if args.report_json:_write_gate_report(args.report_json,report)
    for index,batch in enumerate(batches,1):
        print(f'V2 gate batch {index}/{len(batches)}: {len(batch)} tests',flush=True)
        started=time.perf_counter()
        result=_run(['-q',*batch],timeout=args.batch_timeout_seconds)
        batch_result={'index':index,'nodes':batch,'exit_code':result.returncode,
                      'elapsed_seconds':round(time.perf_counter()-started,3),
                      'status':'passed' if result.returncode==0 else 'timeout' if result.returncode==124 else 'failed'}
        report['batches'].append(batch_result)
        if args.report_json:_write_gate_report(args.report_json,report)
        if result.returncode!=0:
            report['status']='failed'
            report['failed_batch']=index
            report['finished_utc']=datetime.now(timezone.utc).isoformat()
            if args.report_json:_write_gate_report(args.report_json,report)
            return result.returncode
    report['status']='passed'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    if args.report_json:_write_gate_report(args.report_json,report)
    print(f'PASS: {len(stable)} stable test nodes, {len(batches)} independently exited pytest processes.',flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
