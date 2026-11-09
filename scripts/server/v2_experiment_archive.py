"""Atomically publish a real V2 execution receipt as an immutable local report.

The browser never supplies a result for archival. All values come from the
server's single-use execution registry, including provenance and input digests.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.server.execution_provenance import ExecutionRegistry, canonical_bytes
from scripts.server.result_traces import chart_trace


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def _write_json(path: Path, value: object) -> None:
    with path.open('wb') as output:
        output.write(canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def _write_gzip(path: Path, value: object) -> None:
    with path.open('wb') as output:
        with gzip.GzipFile(fileobj=output, mode='wb') as archive:
            archive.write(canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def archive_receipt(root: Path, registry: ExecutionRegistry, *,
                    kind: str, receipt: str) -> dict:
    """Consume a one-time server receipt and publish a verifiable report.

    The outer run directory is reserved exclusively; the fully written
    ``reports`` directory is renamed into it in one same-volume operation.
    Old reports, including empty reserved directories, are never overwritten.
    """
    if kind not in ('training', 'evaluation'):
        raise ValueError('未知实验类型')
    frozen = registry.consume(receipt, kind)
    identity = frozen['identity']
    if kind == 'training':
        experiment_id = identity.get('experiment_id')
        if not isinstance(experiment_id, str) or not _SAFE_ID.fullmatch(experiment_id):
            raise ValueError('训练实验编号无效，禁止归档')
    else:
        experiment_id = 'eval_' + uuid.uuid4().hex

    root = Path(root).resolve()
    outputs = root / 'outputs'
    outputs.mkdir(parents=True, exist_ok=True)
    directory = outputs / ('experiment_' + experiment_id)
    if directory.exists() or directory.is_symlink():
        raise FileExistsError('实验编号已存在，禁止覆盖历史实验')

    temporary = Path(tempfile.mkdtemp(prefix='.pending-v2-', dir=outputs))
    made_directory = False
    try:
        reports = temporary / 'reports'
        reports.mkdir()
        provenance = dict(frozen['provenance'])
        provenance['full_result_artifact'] = 'reports/execution_result.json.gz'
        provenance['config_artifact'] = 'reports/execution_config.json'
        _write_gzip(reports / 'execution_result.json.gz', frozen['result'])
        _write_json(reports / 'execution_config.json', frozen['config'])
        if frozen.get('input_snapshot') is not None:
            provenance['input_snapshot_artifact'] = 'reports/execution_inputs.json.gz'
            _write_gzip(reports / 'execution_inputs.json.gz', frozen['input_snapshot'])

        config = frozen['config']
        result = frozen['result']
        algorithm = str(config.get('algorithm') or identity.get('model_algorithm') or '')
        metrics = result.get('metrics') if isinstance(result.get('metrics'), dict) else {}
        summary = {
            'experiment_id': experiment_id,
            'experiment': {
                'experiment_id': experiment_id, 'type': kind,
                'run_dir': f'outputs/experiment_{experiment_id}',
                'created_at': datetime.now(timezone.utc).isoformat(),
            },
            'provenance': provenance,
            'results': [{
                'detector': algorithm,
                'status': 'success',
                'metrics': metrics,
                'chart_trace': chart_trace(result),
            }],
        }
        if kind == 'training':
            summary['training'] = {
                'algorithm': algorithm,
                'dataset': config.get('dataset'),
                'table': 'v2_channels',
                'model_dir': identity.get('model_dir'),
                'model_file_path': identity.get('model_file_path'),
                'experiment_id': experiment_id,
            }
        else:
            summary['evaluation'] = {
                'model_id': identity.get('model_id'),
                'model_name': identity.get('model_name'),
                'model_algorithm': algorithm,
                'eval_dataset': config.get('dataset'),
                'eval_table': 'v2_channels',
                'evaluation_mode': config.get('evaluation_mode', 'supervised'),
                'ground_truth_available': config.get('evaluation_mode') != 'score_only',
            }
        _write_json(reports / 'summary.json', summary)

        # mkdir is exclusive even when another process has an empty directory;
        # rename only the completed reports so readers see an all-or-none set.
        directory.mkdir(exist_ok=False)
        made_directory = True
        os.replace(reports, directory / 'reports')
        return {
            'experiment_id': experiment_id,
            'type': kind,
            'archive_status': 'complete',
            'summary_path': f'outputs/experiment_{experiment_id}/reports/summary.json',
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if made_directory and not (directory / 'reports').is_dir():
            # Only discard our own empty reservation; another published report
            # cannot be removed by this cleanup path.
            directory.rmdir()
