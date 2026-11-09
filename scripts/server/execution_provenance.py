"""Server-owned execution receipts and bounded, auditable experiment provenance.

Receipts are single-use capabilities, not claims supplied by a browser. The
in-memory store intentionally expires on server restart; completed task results
remain viewable but cannot be silently re-archived as trusted executions.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

import hashlib
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    """Deterministic encoding for JSON-shaped data and results."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=str, allow_nan=False).encode('utf-8')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def implementation_digest(root: Path) -> dict[str, Any]:
    """Hash the actual packaged detector and evaluation implementations."""
    components = []
    for relative in ('detectors', 'configs/detectors', 'scripts/core',
                     'scripts/server/event_scoring.py', 'evaluator'):
        target = root / relative
        paths = sorted(target.rglob('*')) if target.is_dir() else [target]
        for path in paths:
            if path.is_file() and path.suffix.lower() in ('.py', '.yaml', '.yml'):
                components.append([path.relative_to(root).as_posix(), hash_file(path)])
    return {'sha256': sha256_bytes(canonical_bytes(components)),
            'file_count': len(components), 'scope': 'packaged_detector_and_evaluator_sources'}


def _quote(name: str) -> str:
    if not isinstance(name, str) or not name or '\x00' in name:
        raise ValueError('无效数据表名')
    return '"' + name.replace('"', '""') + '"'


def source_tables(config: dict[str, Any]) -> list[str]:
    """Use explicit physical table selection; never guess by model ID or filename."""
    candidates: list[Any] = []
    for key in ('sourceTables', 'tables', 'logicalTables'):
        selected = config.get(key)
        if isinstance(selected, list):
            candidates.extend(selected)
    # Phase 3: the selected representatives are NOT the complete data lineage.
    # Include all physical fragments from every role-validated logical channel.
    multi = config.get('logicalMultiSources')
    if isinstance(multi, list):
        for channel in multi:
            if isinstance(channel, dict) and isinstance(channel.get('tables'), list):
                candidates.extend(channel['tables'])
    for key in ('trainTable', 'testTable', 'table'):
        if config.get(key):
            candidates.append(config[key])
    return list(dict.fromkeys(x for x in candidates if isinstance(x, str) and x))


def data_fingerprint(root: Path, db_path: Path | None,
                     config: dict[str, Any]) -> dict[str, Any]:
    """Stream SHA256 of selected physical rows with column/type boundaries.

    Snapshot is recorded at the point of observation; if data can mutate
    concurrently, a post-run hash is NOT a guarantee of the data read by fit.
    """
    if db_path is None:
        return {'status': 'unavailable', 'reason': 'no_managed_database'}
    db_path = db_path.resolve()
    if not is_relative_to(db_path, root) or not db_path.is_file():
        return {'status': 'unavailable', 'reason': 'invalid_managed_database'}
    names = source_tables(config)
    if not names:
        return {'status': 'unavailable', 'reason': 'no_selected_tables'}
    entries = []
    try:
        with sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True) as conn:
            available = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in names:
                if name not in available:
                    entries.append({'table': name, 'status': 'missing'})
                    continue
                digest = hashlib.sha256()
                columns = [row[1] for row in conn.execute('PRAGMA table_info(' + _quote(name) + ')')]
                digest.update(canonical_bytes(columns) + b'\n')
                count = 0
                # ORDER BY rowid provides a stable row snapshot for normal
                # AAS tables. WITHOUT ROWID is not currently an AAS format.
                for row in conn.execute('SELECT * FROM ' + _quote(name) + ' ORDER BY rowid'):
                    digest.update(canonical_bytes(row) + b'\n')
                    count += 1
                entries.append({'table': name, 'status': 'ok', 'rows': count,
                                'sha256': digest.hexdigest()})
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {'status': 'unavailable', 'reason': type(exc).__name__}
    return {'status': 'complete' if all(x['status'] == 'ok' for x in entries) else 'partial',
            'database': db_path.relative_to(root.resolve()).as_posix(),
            'tables': entries, 'snapshot_semantics': 'physical_rows_at_observation_time'}


def model_fingerprint(root: Path, model_dir: Path | None) -> dict[str, Any]:
    """Digest persisted model artifacts, excluding transient caches."""
    if model_dir is None:
        return {'status': 'not_applicable'}
    try:
        target = Path(model_dir).resolve()
        if not is_relative_to(target, root) or not target.is_dir():
            return {'status': 'unavailable', 'reason': 'invalid_model_directory'}
        files = [[p.relative_to(target).as_posix(), hash_file(p)]
                 for p in sorted(target.rglob('*')) if p.is_file() and
                 '__pycache__' not in p.parts]
        return {'status': 'complete', 'file_count': len(files),
                'sha256': sha256_bytes(canonical_bytes(files))}
    except (ValueError, OSError) as exc:
        return {'status': 'unavailable', 'reason': type(exc).__name__}


def runtime_versions() -> dict[str, Any]:
    """Snapshot reported runtime package versions without importing heavy ML stacks."""
    dependencies = {}
    for name in ('numpy', 'scipy', 'pandas', 'scikit-learn', 'torch',
                 'PyYAML', 'joblib'):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    return {'python': sys.version.split()[0], 'platform': platform.platform(),
            'dependencies': dependencies}



def randomness_evidence(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Document effective *detector-owned* seeds, never reseed a web worker.

    AAS does not set process-wide NumPy/Python/Torch RNG state. Plugins and
    PyTorch/CUDA may have additional randomness not covered by this record.
    """
    if kind != 'training':
        return {'scope': 'published_model_inference', 'control': 'not_applied_by_platform',
                'note': '评估沿用已保存模型状态，AAS 不修改模型内部随机过程'}
    algorithm = str(config.get('algorithm') or '').strip().lower()
    params = config.get('algorithmParams') or {}
    if not isinstance(params, dict):
        return {'control': 'unknown', 'reason': 'invalid_algorithm_parameters'}
    if algorithm in ('mlp_detector', 'isolation_forest_detector'):
        raw = params.get('random_state', 42)
        if raw is None:
            raw = 42
        try:
            seed = int(raw)
        except (ValueError, TypeError, OverflowError):
            return {'control': 'unknown', 'reason': 'invalid_random_state'}
        return {'control': 'detector_local', 'algorithm': algorithm,
                'parameter': 'random_state', 'effective_seed': seed,
                'defaulted': 'random_state' not in params,
                'scope': ('numpy_local_generator_for_weight_initialization' if algorithm == 'mlp_detector'
                          else 'sklearn_estimator_random_state'),
                'guarantee': 'seed_configuration_recorded_not_bitwise_reproduction'}
    return {'control': 'algorithm_defined', 'algorithm': algorithm,
            'requested_seed': params.get('random_state'),
            'guarantee': 'platform_does_not_claim_seed_control_for_this_detector'}


class ExecutionRegistry:
    """Bounded, single-use server-owned records scoped to one app process."""

    def __init__(self, ttl_seconds: int = 1800, max_pending: int = 32):
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending

    def issue(self, *, kind: str, config: dict[str, Any], result: dict[str, Any],
              identity: dict[str, Any], root: Path, db_path: Path | None = None,
              model_dir: Path | None = None,
              pre_data: dict[str, Any] | None = None,
              input_evidence: dict[str, Any] | None = None,
              input_snapshot: dict[str, Any] | None = None) -> str:
        if kind not in ('training', 'evaluation'):
            raise ValueError('Unsupported execution kind')
        # JSON round-trip freezes the server-side results independent of callers.
        frozen_config = json.loads(canonical_bytes(config))
        frozen_result = json.loads(canonical_bytes(result))
        evidence = json.loads(canonical_bytes(input_evidence or
                              {'snapshot_status': 'not_captured'}))
        if input_snapshot is not None:
            from scripts.core.input_evidence import verify_input_snapshot
            if not verify_input_snapshot(input_snapshot, evidence):
                raise ValueError('实际输入矩阵快照未通过完整性检查')
        elif evidence.get('snapshot_status') == 'complete':
            raise ValueError('实际输入矩阵声明完整，但缺少快照数据')
        after = data_fingerprint(root, db_path, frozen_config)
        before = pre_data or {'status': 'unavailable', 'reason': 'not_captured'}
        verified = before.get('status') == 'complete' and after.get('status') == 'complete'
        changed = (before.get('tables') != after.get('tables')) if verified else None
        provenance = {
            'format_version': 1, 'authority': 'server_execution',
            'execution_type': kind, 'execution_timestamp_unix': time.time(),
            'result_sha256': sha256_bytes(canonical_bytes(frozen_result)),
            'config_sha256': sha256_bytes(canonical_bytes(frozen_config)),
            'algorithm_implementation': implementation_digest(root),
            'runtime': runtime_versions(),
            'randomness': randomness_evidence(kind, frozen_config),
            'evaluation_protocol': {'name': 'aas_event_scoring',
                                    'sha256': hash_file(Path(__file__).with_name('event_scoring.py'))},
            'input_before': before, 'input_after': after,
            'input_changed_during_execution': changed,
            'input_digest_note': (
                ('Physical tables were observed before and after execution. ' if verified else
                 'Physical table before/after comparison was not available; '
                 'input_changed_during_execution is unknown, not verified false. ')
                + 'effective_model_inputs hashes the actual post-alignment and '
                  'post-scaling arrays and archives bounded snapshots when available. '
                  'No guarantee of identical rerun randomness or environment.'),
            'model_artifacts': model_fingerprint(root, model_dir),
            'effective_model_inputs': evidence,
        }
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            if len(self._pending) >= self.max_pending:
                # Oldest receipt is expendable; never write an unverified report.
                oldest = min(self._pending, key=lambda key: self._pending[key]['issued'])
                del self._pending[oldest]
            self._pending[token] = {'kind': kind, 'config': frozen_config,
                                    'result': frozen_result, 'identity': dict(identity),
                                    'provenance': provenance, 'input_snapshot': input_snapshot,
                                    'issued': time.monotonic()}
        return token

    def _prune(self) -> None:
        now = time.monotonic()
        for token, record in list(self._pending.items()):
            if now - record['issued'] > self.ttl_seconds:
                del self._pending[token]

    def consume(self, token: str, kind: str) -> dict[str, Any]:
        if not isinstance(token, str) or not token:
            raise ValueError('缺少服务器签发的执行凭证')
        with self._lock:
            self._prune()
            record = self._pending.get(token)
            if record is None or record['kind'] != kind:
                raise ValueError('执行凭证不存在、已使用、已过期或任务类型不一致')
            del self._pending[token]
            return record
