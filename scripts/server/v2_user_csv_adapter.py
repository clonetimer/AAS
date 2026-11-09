"""V2 source importer adapter for persistent user-owned CSV data.

The existing wide-source importer is reused against the isolated user workspace
V2 database. It never resolves or writes the configured historical database.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from scripts.server import v2_user_csv_workspace as workspace
from scripts.server.telemetry_v2_source_import import source_operation_file


def _validate_metadata(metadata: dict[str, Any], role: str) -> dict[str, Any]:
    if role not in ('train', 'test'):
        raise ValueError('用户 CSV 用途只能是 train 或 test')
    if not isinstance(metadata, dict) or metadata.get('operation') not in ('inspect', 'preview', 'commit'):
        raise ValueError('CSV 操作元数据无效')
    result = dict(metadata)
    if result.get('operation') == 'inspect':
        return result
    if result.get('format_confirmed') is not True:
        raise ValueError('必须明确确认 CSV 编码和分隔符')
    config = result.get('config')
    if not isinstance(config, dict):
        raise ValueError('缺少 CSV 列映射配置')
    if config.get('role') != role:
        raise ValueError('CSV 用途与列映射中的数据角色不一致')
    if role == 'train' and config.get('unlabeled_test_confirmed'):
        raise ValueError('训练 CSV 不允许使用测试标签确认')
    if role == 'test' and config.get('all_normal_confirmed'):
        raise ValueError('测试 CSV 不允许使用训练正常标签确认')
    if not isinstance(config.get('namespace'), str) or not config['namespace'].startswith('USER_CSV_'):
        raise ValueError('用户 CSV 必须使用独立命名空间')
    result['filename'] = result.get('filename') or 'user-upload.csv'
    return result


def inspect_or_preview(root: Path, source_path: Path, metadata: dict[str, Any], *,
                       source_digest: str, source_size: int, role: str,
                       progress=None, cancel_requested=None) -> dict[str, Any]:
    meta = _validate_metadata(metadata, role)
    target = workspace.initialize(root)
    result = source_operation_file(target, source_path, meta,
        source_digest=source_digest, source_size=source_size,
        progress=progress, cancel_requested=cancel_requested)
    return {**result, 'storage_scope': 'user_csv_workspace',
            'historical_database_touched': False}


def _existing_import(path: Path, namespace: str, digest: str) -> dict[str, Any] | None:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        source = db.execute('SELECT id,source_row_count,source_name FROM v2_sources WHERE namespace=? AND source_sha256=?',
                            (namespace, digest)).fetchone()
        if source is None:
            return None
        channels = db.execute('''SELECT c.id AS channel_id,g.id AS segment_id,
                c.subsystem,c.device,c.variable,c.unit,g.source_table AS source_column
            FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
            WHERE g.source_id=? ORDER BY g.id''', (source['id'],)).fetchall()
        labels = db.execute('SELECT label_int,COUNT(*) FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id WHERE g.source_id=? GROUP BY label_int',
                            (source['id'],)).fetchall()
    label_values = {row[0] for row in labels}
    label_status = ('unknown' if label_values == {None} else
                    'confirmed_normal' if label_values == {0} else 'provided')
    return {'status': 'imported', 'source_id': source['id'],
            'filename': source['source_name'], 'row_count': source['source_row_count'],
            'column_count': len(channels), 'label_status': label_status,
            'channels': [dict(row) for row in channels], 'sha256': digest}


def commit_user_csv(root: Path, raw: bytes, filename: str, metadata: dict[str, Any], *,
                    role: str, import_key: str | None = None,
                    progress=None, cancel_requested=None, before_commit=None,
                    task_id: str | None = None, task_status_lookup=None) -> dict[str, Any]:
    meta = _validate_metadata(metadata, role)
    if meta.get('operation') != 'commit':
        raise ValueError('用户 CSV 保存只接受 commit 操作')
    if meta.get('filename') != filename:
        raise ValueError('预检文件名与上传文件不一致')
    path = workspace.initialize(root)
    digest = hashlib.sha256(raw).hexdigest()
    config = meta['config']
    namespace = config['namespace']
    config_digest = hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    key = import_key or hashlib.sha256(f'{role}:{namespace}:{digest}:{config_digest}'.encode()).hexdigest()
    stored = workspace.import_csv(root, raw, filename, role,
        {'config': config, 'csv_encoding': meta.get('csv_encoding'),
         'csv_delimiter': meta.get('csv_delimiter')},
        format_confirmed=True, import_key=key)
    if stored['import_status'] == 'imported' and stored.get('source_id'):
        return {**stored.get('import_result', {}), 'dataset_id': stored['id'],
                'storage_scope': 'user_csv_workspace', 'historical_database_touched': False}

    # Recover the narrow crash window: V2 transaction committed, but the upload
    # registry did not yet record source_id. The namespace+digest pair is unique.
    existing = _existing_import(path, namespace, digest)
    if existing is not None:
        workspace.set_import_result(root, stored['id'], existing, status='imported')
        return {**existing, 'dataset_id': stored['id'],
                'storage_scope': 'user_csv_workspace', 'historical_database_touched': False}

    workspace.claim_import(root, stored['id'], task_id=task_id, status_lookup=task_status_lookup)
    try:
        with tempfile.TemporaryDirectory(prefix='aas-user-csv-') as tmp:
            staged = Path(tmp) / filename
            staged.write_bytes(raw)
            result = source_operation_file(path, staged, meta,
                source_digest=digest, source_size=len(raw), progress=progress,
                cancel_requested=cancel_requested, before_commit=before_commit)
        if result.get('status') != 'imported' or type(result.get('source_id')) is not int:
            raise RuntimeError('独立工作区 CSV 导入未返回有效逻辑来源')
        enriched = {**result, 'dataset_id': stored['id'], 'role': role,
                    'workspace_database': workspace.database_reference(),
                    'storage_scope': 'user_csv_workspace',
                    'historical_database_touched': False}
        workspace.set_import_result(root, stored['id'], enriched, status='imported')
        return enriched
    except Exception as exc:
        workspace.set_import_result(root, stored['id'],
            {'error': f'{type(exc).__name__}: {str(exc)[:500]}'}, status='failed')
        raise
