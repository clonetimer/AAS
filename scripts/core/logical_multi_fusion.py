"""Align independently concatenated telemetry channels without persistent wide tables."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.core.multi_table_fusion import MAX_ROWS_PER_SOURCE, fuse_sources
from scripts.server.continuous_series import query_continuous_series
from scripts.server.logical_selection import normalize_identity, select_role_tables
from scripts.server.services import sqlite_tables
from scripts.server.sqlite_guard import sqlite_connection


def resolve_logical_sources(db_path: Path, identities: list[dict[str, str]], *,
                            role: str) -> list[dict[str, Any]]:
    """Resolve ordered channel identities into explicitly role-validated shards."""
    if not isinstance(identities, list) or not 2 <= len(identities) <= 16:
        raise ValueError('跨表联合特征必须选择 2～16 个逻辑参量')
    normalized = [normalize_identity(item) for item in identities]
    unique = [tuple(item[k] for k in ('model','subsystem','device','variable')) for item in normalized]
    if len(set(unique)) != len(unique):
        raise ValueError('不能将同一个逻辑参量重复作为不同特征')
    items = sqlite_tables(db_path)
    return [{'identity': identity, 'tables': select_role_tables(items, identity, role, require_both_labels=False)}
            for identity in normalized]


def fuse_logical_sources(db_path: Path, sources: list[dict[str, Any]], *,
                         role: str, simulation_db_path: Path,
                         time_start: Any = None, time_end: Any = None,
                         tolerance_seconds: float = 0, alignment_method: str = 'nearest',
                         timeline_mode: str = 'anchor', grid_interval_seconds: float = 0,
                         label_alignment_method: str = 'matched') -> tuple[list[dict], dict]:
    """One SQLite read snapshot; exactly one fused feature per logical channel.

    A changed partition membership invalidates the execution rather than silently
    adding or dropping physical fragments between scheduling and execution.
    """
    if role not in ('train', 'test'):
        raise ValueError('跨表融合必须明确 train/test 用途')
    if label_alignment_method == 'interval':
        raise ValueError('跨表联合暂不支持事件区间标签映射')
    if not isinstance(sources, list) or not 2 <= len(sources) <= 16:
        raise ValueError('跨表联合特征必须选择 2～16 个逻辑参量')
    identities = [normalize_identity(item.get('identity')) for item in sources]
    if len({tuple(identity.values()) for identity in identities}) != len(identities):
        raise ValueError('不能重复选择同一个逻辑参量')
    # Validate current role memberships before opening the shared data snapshot.
    current = resolve_logical_sources(db_path, identities, role=role)
    virtual_rows = {}
    virtual_signatures = {}
    manifests = []
    names = []
    for index, (expected, item) in enumerate(zip(sources, current), start=1):
        if not isinstance(expected.get('tables'), list) or not expected['tables'] or any(
                not isinstance(name, str) for name in expected['tables']):
            raise ValueError('逻辑参量缺少物理来源分片清单')
        if expected['tables'] != item['tables']:
            raise ValueError('逻辑参量来源分片或数据用途发生变化，请重新选择')
        names.append(f'logical_{index}')
    with sqlite_connection(db_path, simulation_db_path=simulation_db_path) as conn:
        conn.execute('BEGIN')
        for index, item in enumerate(current):
            # Retain tolerance-neighbor points outside the requested range,
            # letting the shared aligner apply the actual window consistently.
            # This is safe for non-fixed anchor mode, too.
            # Anchor-mode can window-prune at the storage layer. Fixed-grid
            # matching also needs near-boundary samples; retain all for that
            # mode until tolerance-expanded SQL bounds are implemented.
            slice_start = time_start if timeline_mode == 'anchor' else None
            slice_end = time_end if timeline_mode == 'anchor' else None
            info = query_continuous_series(db_path, item['identity'],
                start_time=slice_start, end_time=slice_end,
                limit=0, connection=conn, simulation_db_path=simulation_db_path,
                included_tables=item['tables'])
            if [entry['table'] for entry in info['sources']] != item['tables']:
                raise ValueError('逻辑参量物理分片变化，请重新选择')
            if info['matched_count'] > MAX_ROWS_PER_SOURCE:
                raise ValueError(f'单个逻辑参量超过 {MAX_ROWS_PER_SOURCE} 点，请缩小数据库或分段分析')
            info = query_continuous_series(db_path, item['identity'],
                start_time=slice_start, end_time=slice_end,
                limit=info['matched_count'], _max_page=MAX_ROWS_PER_SOURCE,
                connection=conn, simulation_db_path=simulation_db_path,
                included_tables=item['tables'])
            key = names[index]
            virtual_rows[key] = info['rows']
            virtual_signatures[key] = item['identity']
            manifests.append({'identity': item['identity'], 'tables': item['tables'],
                'physical_sources': info['sources'], 'deduplicated_count': info['matched_count'],
                'duplicate_count': info['duplicate_count'], 'min': info['min'], 'max': info['max']})
        rows, alignment = fuse_sources(conn, names, time_start=time_start,
            time_end=time_end, tolerance_seconds=tolerance_seconds,
            alignment_method=alignment_method, timeline_mode=timeline_mode,
            grid_interval_seconds=grid_interval_seconds,
            label_alignment_method=label_alignment_method,
            virtual_rows=virtual_rows, virtual_signatures=virtual_signatures)
        conn.commit()
    if role == 'test' and {int(row['label']) for row in rows} != {0, 1}:
        raise ValueError('对齐后的逻辑测试集必须同时包含正常与故障样本')
    alignment['logical_sources'] = manifests
    alignment['source_mapping'] = dict(zip(alignment['feature_columns'], identities))
    alignment['fusion_kind'] = 'concatenate_each_channel_then_align'
    alignment['data_role'] = role
    return rows, alignment
