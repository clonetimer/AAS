"""Complete, read-only V2 telemetry export of selected channels and time range.

The browser chart intentionally caps drawing data; this exporter does not use its
preview rows.  It writes a long-form UTF-8 BOM CSV to a temporary file under one
SQLite read transaction.  Independent satellites and channels never get joined.
"""
from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from scripts.server import telemetry_v2_access as access
from scripts.server.telemetry_v2_governance import active_clause

COLUMNS = (
    'channel_id', 'namespace', 'satellite_model', 'satellite_instance',
    'subsystem', 'component', 'parameter', 'unit', 'data_role', 'time_domain',
    'timestamp', 'relative_seconds', 'value', 'label', 'source_table',
    'segment_id', 'source_row_ordinal',
)


def _spreadsheet_safe(value):
    """Prevent untrusted string identities from becoming spreadsheet formulas."""
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def build_selection_export(database: Path, *, role: str, channel_ids: list[int],
                           start=None, end=None):
    """Return (binary file, MIME, filename). Caller closes the temporary file.

    Full matching samples are exported, not the chart's first 3,000 plotted
    samples. Repeated equal values/tags for one channel/time are deduplicated;
    conflicting duplicates reject the entire download.
    """
    access._role(role)
    if role not in ('train', 'test'):
        raise ValueError('请选择训练或测试数据用途')
    if not isinstance(channel_ids, list) or not 1 <= len(channel_ids) <= access.MAX_CHANNELS:
        raise ValueError('请选择 1～8 个参量')
    if any(type(cid) is not int or cid <= 0 for cid in channel_ids) or len(set(channel_ids)) != len(channel_ids):
        raise ValueError('参量 ID 必须为不重复的正整数')
    output = tempfile.TemporaryFile(mode='w+b')
    try:
        with access.open_database(database) as conn:
            conn.execute('BEGIN')  # single consistent read snapshot
            stream = io.TextIOWrapper(output, encoding='utf-8-sig', newline='', write_through=True)
            try:
                writer = csv.writer(stream)
                writer.writerow(COLUMNS)
                exported_rows=0
                for cid in channel_ids:
                    channel = conn.execute('SELECT * FROM v2_channels WHERE id=?', (cid,)).fetchone()
                    if channel is None:
                        raise ValueError(f'参量 #{cid} 不存在')
                    active = active_clause(conn, 'g')
                    segments = conn.execute('''SELECT g.id, g.time_domain
                        FROM v2_segments g WHERE g.channel_id=? AND g.kind='telemetry'
                        AND g.usable_for_timeseries=1 AND ''' + access._run_view(conn, role)
                        + access._policy(role) + active, (cid, role)).fetchall()
                    if not segments:
                        raise ValueError(f'{channel["variable"]} 在当前用途下没有有效采样分片')
                    domains = {seg['time_domain'] for seg in segments}
                    if len(domains) != 1:
                        raise ValueError(f'{channel["variable"]} 存在多个时间域，无法直接导出')
                    domain = next(iter(domains))
                    access._clock_contracts(conn, [seg['id'] for seg in segments], domain)
                    time_key = access._time_expr(domain)
                    lower = access._timestamp(start, domain)
                    upper = access._timestamp(end, domain)
                    if lower is not None and upper is not None and lower > upper:
                        raise ValueError('开始时间不能晚于结束时间')
                    where = ['s.channel_id=?', "g.kind='telemetry'", 'g.usable_for_timeseries=1',
                             access._run_view(conn, role), '1=1' + active]
                    args = [cid, role]
                    if role in ('train', 'test'):
                        where.append("g.role_provenance NOT IN ('name_hint_only','database_hint_only')")
                    if lower is not None:
                        where.append(time_key + ' >= ?')
                        args.append(lower)
                    if upper is not None:
                        where.append(time_key + ' <= ?')
                        args.append(upper)
                    sql = f'''SELECT {time_key} AS time_key, s.timestamp_raw, s.value_num,
                        s.label_int, s.source_ordinal, g.source_table, g.id AS segment_id
                        FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id
                        WHERE {' AND '.join(where)}
                        ORDER BY {time_key},s.segment_id,s.source_ordinal'''
                    previous = None
                    for sample in conn.execute(sql, args):
                        key = sample['time_key']
                        value = sample['value_num']
                        label = sample['label_int']
                        if previous is not None and previous[0] == key:
                            if previous[1:] != (value, label):
                                raise ValueError(f'{channel["variable"]} 在时间 {sample["timestamp_raw"]} 存在冲突采样，拒绝导出')
                            continue
                        previous = (key, value, label)
                        timestamp = access.display_time(key) if domain == 'numeric_unspecified' else sample['timestamp_raw']
                        row = [
                            cid, channel['namespace'], channel['model'], channel['satellite_instance'],
                            channel['subsystem'], channel['device'], channel['variable'], channel['unit'],
                            role, domain, timestamp, key if domain == 'numeric_unspecified' else '',
                            value, label, sample['source_table'], sample['segment_id'], sample['source_ordinal'],
                        ]
                        writer.writerow([_spreadsheet_safe(item) for item in row])
                        exported_rows+=1
                if exported_rows == 0:
                    raise ValueError('所选参量在当前时间范围内没有可导出的采样，请调整时间范围')
                stream.flush()
            finally:
                stream.detach()
            conn.commit()
        output.seek(0)
        return output, 'text/csv; charset=utf-8', 'AAS_所选遥测参量.csv'
    except BaseException:
        output.close()
        raise
