"""Read a logical telemetry channel across existing physical SQLite tables.

This is *time concatenation* of one identity, not feature alignment.  It is
read-only and does not migrate, merge or modify any source measurement table.
"""
from __future__ import annotations

import sqlite3
import math
from pathlib import Path
from contextlib import nullcontext
from typing import Any

from scripts.server import series_catalog as catalog
from scripts.server import partition_index
from scripts.server.sqlite_guard import sqlite_connection

MAX_PARTITIONS = 128
MAX_PAGE = 5000
MAX_TRAIN_ROWS = 250000


def query_continuous_series(
    db_path: Path, identity: dict[str, str], *,
    start_time: Any = None, end_time: Any = None,
    limit: int = 400, offset: int = 0, simulation_db_path: Path,
    connection: sqlite3.Connection | None = None, _max_page: int = MAX_PAGE,
    included_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Return globally time-sorted records from every matching single-value table.

    Exact four-level identity is required.  Same-timestamp identical records
    are presented once; different values/labels at the same timestamp produce
    an explicit error instead of silently choosing a source.
    """
    if not all(isinstance(identity.get(key), str) and identity[key].strip()
               for key in catalog.IDENTITY):
        raise ValueError('必须完整指定型号、分系统、部件、参量')
    identity = {key: identity[key].strip() for key in catalog.IDENTITY}
    if isinstance(limit, bool) or isinstance(offset, bool) or not isinstance(limit, int) or not isinstance(offset, int):
        raise ValueError('分页参数必须为整数')
    if limit < 0 or limit > _max_page or offset < 0:
        raise ValueError(f'limit 必须在 0～{_max_page} 范围内，offset 不得为负数')
    with (nullcontext(connection) if connection is not None else
          sqlite_connection(db_path, simulation_db_path=simulation_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        candidates: list[dict[str, Any]] = []
        time_types: set[str] = set()
        text_shapes: set[str] = set()
        for name in names:
            if included_tables is not None and name not in included_tables:
                continue
            if (not name or name.startswith('aas_') or name.startswith('sqlite_')
                    or name.lower().endswith('_clean')):
                continue
            identity_found = partition_index.eligible(conn, name)
            if identity_found != identity:
                continue
            summary, _cached = partition_index.inspect(conn, name, identity)
            if not summary['row_count']:
                continue
            kind = summary['timestamp_kind']
            if kind == 'text':
                for boundary in (str(summary['time_start']), str(summary['time_end'])):
                    if len(boundary) > 10 and boundary[10] in (' ', 'T'):
                        text_shapes.add(boundary[10])
            elif kind != 'number':
                raise ValueError(f'{name} 存在混合类型或 NULL 时间戳，请先规范化')
            meta = {'t0': summary['time_start'], 't1': summary['time_end'], 'n': summary['row_count']}
            time_types.add(kind)
            candidates.append({'table': name, 'time_start': meta['t0'],
                               'time_end': meta['t1'], 'row_count': int(meta['n'])})
        if not candidates:
            raise ValueError('没有找到该参量的单参量数据表')
        if len(candidates) > MAX_PARTITIONS:
            raise ValueError(f'关联分片超过 {MAX_PARTITIONS} 张，需先归档或优化分片目录')
        if len(time_types) != 1 or len(text_shapes) > 1:
            raise ValueError('参量分片的时间类型或文本时间格式不一致，请先规范化')
        kind = next(iter(time_types))
        if kind == 'number':
            for bound in (start_time, end_time):
                if bound not in (None, ''):
                    try:
                        if not math.isfinite(float(bound)):
                            raise ValueError('时间界限必须为有限数值')
                    except (ValueError, TypeError) as exc:
                        raise ValueError('数值时间戳筛选范围无效') from exc
        if start_time not in (None, '') and end_time not in (None, ''):
            if kind == 'number':
                try:
                    if not math.isfinite(float(start_time)) or not math.isfinite(float(end_time)) or float(start_time) > float(end_time):
                        raise ValueError('起始时间不能晚于结束时间')
                except (TypeError, ValueError) as exc:
                    raise ValueError('时间范围必须为数值且起始时间不晚于结束时间') from exc
            elif str(start_time) > str(end_time):
                raise ValueError('起始时间不能晚于结束时间')
        # Filter per partition *before* union; do not filter an arbitrary first
        # page.  All SQL identifiers originate from validated SQLite schema.
        parts: list[str] = []
        params: list[Any] = []
        sources = []
        for item in candidates:
            table = item['table']
            qname = catalog.quote(table)
            conditions, args = [], []
            if start_time not in (None, ''):
                conditions.append('"timestamp" >= ?')
                args.append(float(start_time) if kind == 'number' else start_time)
            if end_time not in (None, ''):
                conditions.append('"timestamp" <= ?')
                args.append(float(end_time) if kind == 'number' else end_time)
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ''
            # Time-coverage pruning: wholly disjoint partitions need no row scan.
            if ((start_time not in (None, '') and
                 item['time_end'] < (float(start_time) if kind == 'number' else start_time)) or
                (end_time not in (None, '') and
                 item['time_start'] > (float(end_time) if kind == 'number' else end_time))):
                n = 0
            else:
                n = conn.execute(f'SELECT COUNT(*) FROM {qname}{where}', args).fetchone()[0]
            sources.append({**item, 'matched_count': int(n)})
            if not n:
                continue
            parts.append(f'SELECT "timestamp", "value", "label", ? AS source_table '
                         f'FROM {qname}{where}')
            params.extend([table, *args])
        min_time = min(item['time_start'] for item in candidates)
        max_time = max(item['time_end'] for item in candidates)
        if not parts:
            return {'columns': ['timestamp', *catalog.IDENTITY, 'value', 'label', 'source_table'],
                    'rows': [], 'total_count': sum(i['row_count'] for i in sources),
                    'matched_count': 0, 'truncated': False, 'sources': sources,
                    'min': min_time, 'max': max_time, 'value_type': kind,
                    'identity': identity, 'duplicate_count': 0}
        union = ' UNION ALL '.join(parts)
        # Duplicate equal-value observations may be replicated during reimport;
        # they represent one logical sample. Different data must be resolved
        # by the user rather than silently overwriting evidence.
        conflicts = conn.execute(
            f'SELECT timestamp FROM ({union}) GROUP BY timestamp '
            f'HAVING COUNT(DISTINCT quote(value) || char(31) || quote(label)) > 1 '
            f'LIMIT 3', params).fetchall()
        if conflicts:
            examples = ', '.join(str(row[0]) for row in conflicts)
            raise ValueError(f'同一参量不同分片在相同时间戳的数据或标签冲突：{examples}；请清理冲突后查询')
        matched = conn.execute(
            f'SELECT COUNT(*) FROM (SELECT timestamp FROM ({union}) GROUP BY timestamp)', params
        ).fetchone()[0]
        physical_matched = sum(item['matched_count'] for item in sources)
        # Enforce deterministic provenance when samples are exact duplicates.
        records = conn.execute(
            f'SELECT timestamp, value, label, MIN(source_table) AS source_table '
            f'FROM ({union}) GROUP BY timestamp ORDER BY timestamp ASC LIMIT ? OFFSET ?',
            [*params, limit, offset],
        ).fetchall()
        rows = [{**{'timestamp': record['timestamp']}, **identity,
                 'value': record['value'], 'label': record['label'],
                 'source_table': record['source_table']}
                for record in records]
        return {'columns': ['timestamp', *catalog.IDENTITY, 'value', 'label', 'source_table'],
                'rows': rows, 'total_count': sum(i['row_count'] for i in sources),
                'matched_count': int(matched), 'truncated': offset + len(rows) < matched,
                'sources': sources, 'min': min_time, 'max': max_time, 'value_type': kind,
                'identity': identity, 'duplicate_count': physical_matched - matched,
                'total_count_kind': 'physical_rows'}


def load_logical_training_rows(db_path: Path, identity: dict[str, str], *,
                               start_time: Any = None, end_time: Any = None,
                               simulation_db_path: Path,
                               expected_tables: list[str] | None = None
                               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bounded full logical snapshot, for training/assessment (not chart paging).

    Read under one SQLite transaction. Preserve exact source manifest and fail
    closed if previously validated source tables have changed membership.
    """
    with sqlite_connection(db_path, simulation_db_path=simulation_db_path) as conn:
        conn.execute('BEGIN')
        info = query_continuous_series(db_path, identity, start_time=start_time,
            end_time=end_time, limit=0, simulation_db_path=simulation_db_path, connection=conn,
            included_tables=expected_tables)
        if info['matched_count'] > MAX_TRAIN_ROWS:
            raise ValueError(f'跨表训练/评估超过 {MAX_TRAIN_ROWS} 点，请缩短时间范围')
        actual = [source['table'] for source in info['sources']]
        if expected_tables is not None and sorted(expected_tables) != sorted(actual):
            raise ValueError('逻辑参量对应的来源表已经变化，请刷新选择并重新发起任务')
        info = query_continuous_series(db_path, identity, start_time=start_time,
            end_time=end_time, limit=info['matched_count'], _max_page=MAX_TRAIN_ROWS,
            simulation_db_path=simulation_db_path, connection=conn, included_tables=expected_tables)
        conn.commit()
    return info['rows'], {key: info[key] for key in ('identity', 'sources', 'min',
        'max', 'matched_count', 'duplicate_count', 'value_type')}
