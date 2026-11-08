"""Bridge user-owned CSV records into an isolated V2 workspace database.

The active historical database is never consulted. The existing V2 wide CSV
importer is reused against user_csv_workspace.sqlite so run-dataset loaders can
consume the same logical-source schema without polluting historical telemetry.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from scripts.server import v2_user_csv_workspace as workspace


def _validate_metadata(metadata: dict[str, Any], role: str) -> dict[str, Any]:
    if role not in ('train', 'test'):
        raise ValueError('用户 CSV 用途只能是 train 或 test')
    if not isinstance(metadata, dict):
        raise ValueError('CSV 导入元数据无效')
    result = dict(metadata)
    if result.get('operation') != 'commit':
        raise ValueError('工作区写入只接受已完成预检的 commit 操作')
    config = result.get('config')
    if not isinstance(config, dict):
        raise ValueError('缺少 CSV 列映射配置')
    if config.get('role') != role:
        raise ValueError('CSV 用途与列映射中的数据角色不一致')
    if role == 'train' and config.get('unlabeled_test_confirmed'):
        raise ValueError('训练 CSV 不允许使用测试标签确认')
    if role == 'test' and config.get('all_normal_confirmed'):
        raise ValueError('测试 CSV 不允许使用训练正常标签确认')
    result['filename'] = result.get('filename') or 'user-upload.csv'
    return result


def commit_dataset(root: Path, dataset_id: str, metadata: dict[str, Any],
                   *, role: str, format_confirmed: bool) -> dict[str, Any]:
    """Commit an already-stored raw CSV to the isolated V2 workspace DB.

    The raw upload must already exist in the persistent workspace and its role
    is immutable. The importer writes logical V2 sources to the workspace DB.
    A failed import leaves the original upload available for correction/retry.
    """
    if format_confirmed is not True:
        raise ValueError('必须明确确认 CSV 编码和分隔符')
    record = workspace.get_dataset(root, dataset_id, include_bytes=True)
    if record['role'] != role:
        raise ValueError('CSV 数据集用途不匹配')
    payload = record['raw_bytes']
    meta = _validate_metadata(metadata, role)
    if meta.get('filename') != record['filename']:
        raise ValueError('预检文件名与已保存文件不一致')
    path = workspace.database_path(root)
    from scripts.server.telemetry_v2_source_import import source_operation_file
    with tempfile.TemporaryDirectory(prefix='aas-user-csv-') as tmp:
        staged = Path(tmp) / record['filename']
        staged.write_bytes(payload)
        result = source_operation_file(
            path, staged, meta, source_digest=record['sha256'], source_size=record['size_bytes'])
    if result.get('status') not in ('imported', 'complete') or not result.get('source_id'):
        raise RuntimeError('独立工作区 CSV 导入未返回有效逻辑来源')
    enriched = {**record['metadata'], 'source_id': result['source_id'],
                'row_count': result.get('row_count'),
                'column_count': result.get('column_count'),
                'label_status': result.get('label_status'),
                'imported_utc': result.get('imported_utc')}
    with workspace._connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE user_csv_datasets SET metadata_json=? WHERE id=?',
                   (json.dumps(enriched, ensure_ascii=False, sort_keys=True), dataset_id))
    return {**result, 'dataset_id': dataset_id, 'role': role,
            'workspace_database': str(path), 'historical_database_touched': False}
