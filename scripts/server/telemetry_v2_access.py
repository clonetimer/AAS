"""Read-only V2 telemetry catalog/query access for the browser.

Do not join distinct source namespaces, roles or time domains implicitly.
All SQL identifiers are fixed; user inputs are bound parameters.  The database
path is administrator-owned and never taken from a browser request.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import math
import re
import sqlite3
import threading

VALID_ROLES = frozenset({'raw','unspecified','train','test','simulation','processed','simulated_fault'})
MAX_CHANNELS = 8
MAX_POINTS = 5000
# UI presentation-only date for relative simulation clocks. It is NOT a claimed
# acquisition UTC clock. Keep time_sort_num intact for matching and provenance.
DISPLAY_ZERO = datetime(2026, 1, 1, 0, 0, 0)

def display_time(seconds):
    return (DISPLAY_ZERO + timedelta(seconds=float(seconds))).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

def relative_seconds(value):
    if value in (None, ''): return None
    try:
        result=float(value)
    except (ValueError, TypeError):
        try:
            parsed=datetime.fromisoformat(str(value).strip().replace('T',' '))
            if parsed.tzinfo is not None: raise ValueError('展示时间请勿填写时区')
            result=(parsed-DISPLAY_ZERO).total_seconds()
        except (ValueError, TypeError) as exc:
            raise ValueError('请使用 YYYY-MM-DD HH:mm:ss 或相对秒数') from exc
    if not math.isfinite(result): raise ValueError('时间必须为有限值')
    return result



@contextmanager
def open_database(path: Path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('V2 数据库未安装或连接失败，请联系管理员检查数据源配置')
    # mode=ro avoids accidental database writes; query_only is defense in depth.
    # Connect explicitly (not via the bare context manager) so the handle is
    # always closed when the caller's `with` block exits; an open handle would
    # block file deletion/renames on Windows.
    conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        version = conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
        if not version or str(version[0]) != '2':
            raise ValueError('这不是受支持的 V2 数据库')
        yield conn
    finally:
        conn.close()


def query_frame_samples(db: Path, *, source_id: int, frame_sequence_id: str,
                        channel_ids: list[int], start: int = 1, end: int | None = None,
                        limit: int = 5000) -> dict:
    """Read one physical frame sequence; equal frame numbers in other sources stay separate."""
    if type(source_id) is not int or source_id < 1:
        raise ValueError('source_id 必须为正整数')
    if not isinstance(frame_sequence_id, str) or not frame_sequence_id.strip():
        raise ValueError('必须指定帧序列标识')
    if (not isinstance(channel_ids, list) or not channel_ids or len(channel_ids) > MAX_CHANNELS
            or any(type(value) is not int or value < 1 for value in channel_ids)
            or len(set(channel_ids)) != len(channel_ids)):
        raise ValueError('请选择不重复的有效参量通道')
    if (type(start) is not int or start < 1 or
            (end is not None and (type(end) is not int or end < start)) or
            type(limit) is not int or not 1 <= limit <= MAX_POINTS):
        raise ValueError('帧范围或数量限制无效')
    placeholders = ','.join('?' for _ in channel_ids)
    where_end = ' AND s.frame_index<=?' if end is not None else ''
    args = [source_id, frame_sequence_id, *channel_ids, start]
    if end is not None:
        args.append(end)
    args.append(limit + 1)
    with open_database(db) as conn:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_samples)')}
        if 'frame_index' not in cols:
            raise ValueError('数据库尚未迁移帧坐标')
        quality_expr='s.quality_flag' if 'quality_flag' in cols else 'NULL AS quality_flag'
        rows = conn.execute(f'''SELECT s.frame_index,s.channel_id,s.value_num,s.label_int,
                    s.timestamp_raw,s.relative_seconds,{quality_expr},s.source_ordinal,g.id AS segment_id
                FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
                WHERE g.source_id=? AND g.frame_sequence_id=? AND g.coordinate_mode='frame'
                    AND s.channel_id IN ({placeholders}) AND s.frame_index>=?{where_end}
                ORDER BY s.frame_index,s.channel_id LIMIT ?''', args).fetchall()
        return {'coordinate_mode': 'frame', 'source_id': source_id,
                'frame_sequence_id': frame_sequence_id, 'truncated': len(rows) > limit,
                'samples': [dict(row) for row in rows[:limit]]}


def _role(value):
    if value not in VALID_ROLES:
        raise ValueError('必须选择明确的数据用途 role')
    return value


def _run_view(conn, role: str) -> str:
    """Labeled simulation runs are *views*, never rewritten train/test segments.

    Train: original train split AND complete NORMAL runs.
    Test: original validation split (NORMAL and FAULT).
    Unlabeled imports are never silently promoted.
    """
    available = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_annotations'").fetchone()
    if not available or role not in ('train', 'test'):
        return 'g.role=?'
    if role == 'train':
        return "(g.role=? OR (g.role='simulation' AND EXISTS (SELECT 1 FROM v2_run_annotations a WHERE a.source_id=g.source_id AND a.split='train' AND a.run_role='NORMAL' AND a.metadata_not_model_features=1)))"
    return "(g.role=? OR (g.role='simulation' AND EXISTS (SELECT 1 FROM v2_run_annotations a WHERE a.source_id=g.source_id AND a.split='validation' AND a.metadata_not_model_features=1)))"


def _policy(role: str) -> str:
    # A name suffix is not authorization to use a split as training/test data.
    return " AND g.role_provenance NOT IN ('name_hint_only','database_hint_only')" if role in ('train','test') else ''



def _unverified_split(source_table: str):
    """Detect unresolved role hints without upgrading them to trusted roles."""
    names=re.findall(r'(?:^|[_-])(train|test|clean)(?=[_-]|$)',source_table.lower())
    return names[0] if names else None

def _timestamp(value, domain):
    if value in (None, ''):
        return None
    if domain == 'numeric_unspecified':
        return relative_seconds(value)
    try:
        moment = datetime.fromisoformat(str(value).strip().replace('Z','+00:00'))
    except ValueError as exc:
        raise ValueError('日期时间格式错误') from exc
    if domain == 'datetime_utc':
        if moment.tzinfo is None:
            raise ValueError('UTC 时间范围必须带 Z 或明确时区')
        utc = moment.astimezone(timezone.utc)
        delta = utc - datetime(1970,1,1,tzinfo=timezone.utc)
        return (delta.days*86400 + delta.seconds)*1000000 + delta.microseconds
    if domain == 'datetime_naive':
        if moment.tzinfo is not None:
            raise ValueError('无时区时间域不能混入带时区时间戳')
        return moment.isoformat(timespec='microseconds')
    raise ValueError('无法查询未知时间域')


def _filter_expr(filters):
    where=[]; args=[]
    for field in ('namespace','satellite_instance','model','subsystem','device','variable'):
        value = filters.get(field)
        if value is not None and value != '':
            where.append('c.'+field+'=?');args.append(value)
    keyword=filters.get('keyword')
    if keyword:
        where.append("(c.variable LIKE ? ESCAPE '\\' OR c.device LIKE ? ESCAPE '\\')")
        pattern='%'+str(keyword).replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        args.extend([pattern,pattern])
    return (' AND '+' AND '.join(where) if where else ''), args


def _catalog_segment_filter(conn, role: str) -> str:
    from scripts.server.telemetry_v2_governance import active_clause
    return ("g.kind='telemetry' AND g.usable_for_timeseries=1 AND " +
            _run_view(conn, role) + _policy(role) + active_clause(conn, 'g'))


def _channel_items_from_rows(conn, rows, role: str, *, start=None, end=None):
    channels = {}
    for row in rows:
        cid = row['id']
        if cid not in channels:
            channels[cid] = {field: row[field] for field in (
                'id', 'namespace', 'satellite_instance', 'model', 'subsystem',
                'device', 'variable', 'unit')}
            channels[cid]['segments'] = []
        channels[cid]['segments'].append(row)

    result = []
    for item in channels.values():
        segments = item.pop('segments')
        domains = {segment['time_domain'] for segment in segments}
        if start not in (None, '') or end not in (None, ''):
            kept = []
            for segment in segments:
                domain = segment['time_domain']
                if domain not in ('datetime_utc', 'datetime_naive', 'numeric_unspecified'):
                    continue
                left = _timestamp(start, domain)
                right = _timestamp(end, domain)
                if left is not None and right is not None and left > right:
                    raise ValueError('开始时间不能晚于结束时间')
                lo = _timestamp(segment['time_start_raw'], domain)
                hi = _timestamp(segment['time_end_raw'], domain)
                if ((left is None or hi is None or hi >= left) and
                        (right is None or lo is None or lo <= right)):
                    field = _time_expr(domain).replace('s.', '')
                    constraints = ['segment_id=?']
                    params = [segment['segment_id']]
                    if left is not None:
                        constraints.append(field + ' >= ?')
                        params.append(left)
                    if right is not None:
                        constraints.append(field + ' <= ?')
                        params.append(right)
                    exists = conn.execute(
                        'SELECT 1 FROM v2_samples WHERE ' + ' AND '.join(constraints) + ' LIMIT 1',
                        params).fetchone()
                    if exists:
                        kept.append(segment)
            if not kept:
                continue
            segments = kept
        item.update(
            segment_count=len(segments),
            row_count=sum(segment['source_row_count'] for segment in segments),
            time_domains=sorted({segment['time_domain'] for segment in segments}),
            time_start=min((segment['time_start_raw'] for segment in segments
                            if segment['time_start_raw']), default=None),
            time_end=max((segment['time_end_raw'] for segment in segments
                          if segment['time_end_raw']), default=None),
            mixed_time_domains=len(domains) > 1,
            unresolved_role_split=(role == 'unspecified' and len({
                _unverified_split(segment['source_table']) for segment in segments}) > 1),
        )
        result.append(item)
    return result


def list_channels(db, *, role='unspecified', filters=None, start=None, end=None,
                  limit=50, offset=0):
    """Return a stable page of logical channels without materializing all segments."""
    role = _role(role)
    filters = filters or {}
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 5000):
        raise ValueError('limit 必须为 1～5000 的整数，内部全量查询可使用 None')
    if type(offset) is not int or offset < 0:
        raise ValueError('offset 必须为非负整数')
    w, args = _filter_expr(filters)
    with open_database(db) as conn:
        segment_filter = _catalog_segment_filter(conn, role)
        eligible = f'EXISTS (SELECT 1 FROM v2_segments g WHERE g.channel_id=c.id AND {segment_filter})'
        base_where = f'WHERE {eligible}{w}'

        # A time-filtered catalog is a less common compatibility path. Keep its
        # exact sample-existence semantics, then page the filtered channel set.
        if start not in (None, '') or end not in (None, ''):
            sql = '''SELECT c.id,c.namespace,c.satellite_instance,c.model,c.subsystem,c.device,c.variable,c.unit,
                     g.id AS segment_id,g.source_table,g.time_domain,g.role,g.role_provenance,
                     g.source_row_count,g.time_start_raw,g.time_end_raw
                     FROM v2_channels c JOIN v2_segments g ON g.channel_id=c.id
                     WHERE ''' + segment_filter + w + ' ORDER BY c.namespace,c.model,c.subsystem,c.device,c.variable,g.id'
            rows = conn.execute(sql, [role, *args]).fetchall()
            all_items = _channel_items_from_rows(conn, rows, role, start=start, end=end)
            total = len(all_items)
            items = all_items[offset:] if limit is None else all_items[offset:offset + limit]
        else:
            total = int(conn.execute(
                'SELECT COUNT(*) FROM v2_channels c ' + base_where, [role, *args]).fetchone()[0])
            page_sql = (
                '''SELECT c.id,c.namespace,c.satellite_instance,c.model,c.subsystem,c.device,c.variable,c.unit
                   FROM v2_channels c ''' + base_where +
                ' ORDER BY c.namespace,c.model,c.subsystem,c.device,c.variable,c.id')
            page_params = [role, *args]
            if limit is not None:
                page_sql += ' LIMIT ? OFFSET ?'
                page_params.extend([limit, offset])
            page_rows = conn.execute(page_sql, page_params).fetchall()
            ids = [int(row['id']) for row in page_rows]
            if ids:
                slots = ','.join('?' for _ in ids)
                sql = '''SELECT c.id,c.namespace,c.satellite_instance,c.model,c.subsystem,c.device,c.variable,c.unit,
                         g.id AS segment_id,g.source_table,g.time_domain,g.role,g.role_provenance,
                         g.source_row_count,g.time_start_raw,g.time_end_raw
                         FROM v2_channels c JOIN v2_segments g ON g.channel_id=c.id
                         WHERE c.id IN (''' + slots + ') AND ' + segment_filter + \
                      ' ORDER BY c.namespace,c.model,c.subsystem,c.device,c.variable,g.id'
                rows = conn.execute(sql, [*ids, role]).fetchall()
                items = _channel_items_from_rows(conn, rows, role)
            else:
                items = []
        has_more = limit is not None and offset + len(items) < total
        return {
            'items': items, 'role': role, 'count': total,
            'limit': limit, 'offset': offset, 'has_more': has_more,
            'next_offset': offset + len(items) if has_more else None,
        }


def channel_time_coverage(db, *, role, channel_ids, mode='intersection'):
    # Read actual usable sample bounds for chosen logical channels.
    # Training/evaluation preserves strict intersection by default. Browser
    # may explicitly request the union so independent runs can be inspected
    # without claiming their samples are temporally aligned.
    role = _role(role)
    if mode not in ('intersection','union'):
        raise ValueError('时间范围模式仅支持 intersection 或 union')
    if not isinstance(channel_ids, list) or not channel_ids or len(channel_ids) > MAX_CHANNELS:
        raise ValueError('请选择 1～8 个参量')
    if any(type(cid) is not int or cid <= 0 for cid in channel_ids) or len(set(channel_ids)) != len(channel_ids):
        raise ValueError('参量 ID 必须为不重复的正整数')
    from scripts.server.telemetry_v2_governance import active_clause
    with open_database(db) as conn:
        entries = []
        for cid in channel_ids:
            rows = conn.execute('''SELECT g.id, g.time_domain FROM v2_segments g
                JOIN v2_channels c ON c.id=g.channel_id
                WHERE c.id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1 AND '''+
                _run_view(conn,role)+_policy(role)+active_clause(conn,'g'), [cid,role]).fetchall()
            if not rows:
                raise ValueError(f'参量 #{cid} 在当前数据用途下没有有效来源')
            per_domain = {row['time_domain'] for row in rows}
            if len(per_domain) != 1 or next(iter(per_domain)) not in ('numeric_unspecified','datetime_naive','datetime_utc'):
                raise ValueError(f'参量 #{cid} 包含多个不同的时间域，无法统一选择日期')
            domain = next(iter(per_domain))
            field = {'numeric_unspecified':'time_sort_num', 'datetime_naive':'time_sort_text',
                     'datetime_utc':'time_utc_us'}[domain]
            bounds = []
            for seg in rows:
                bound = conn.execute(f'''SELECT MIN({field}), MAX({field}) FROM v2_samples
                    WHERE segment_id=? AND {field} IS NOT NULL AND value_num IS NOT NULL''',
                    (seg['id'],)).fetchone()
                if bound[0] is not None:
                    bounds.append((bound[0], bound[1]))
            if not bounds:
                raise ValueError(f'参量 #{cid} 没有有效数值采样')
            entries.append({'channel_id':cid,'time_domain':domain,
                            'start_raw':min(x[0] for x in bounds),
                            'end_raw':max(x[1] for x in bounds),
                            'segment_count':len(bounds)})
        domains = {item['time_domain'] for item in entries}
        if len(domains) != 1:
            raise ValueError('所选参量包含不同时间域，请分开选择')
        domain = next(iter(domains))
        low = (min if mode=='union' else max)(item['start_raw'] for item in entries)
        high = (max if mode=='union' else min)(item['end_raw'] for item in entries)
        def calendar(value, edge):
            if domain == 'numeric_unspecified':
                # HTML datetime-local uses millisecond precision here. Round
                # inward so a picker boundary cannot be outside real samples.
                ms = math.ceil(float(value)*1000 - 1e-8) if edge=='start' else math.floor(float(value)*1000 + 1e-8)
                when = DISPLAY_ZERO + timedelta(milliseconds=ms)
            elif domain == 'datetime_utc':
                micro = int(value)
                ms = (micro+999)//1000 if edge=='start' else micro//1000
                when = datetime(1970,1,1) + timedelta(milliseconds=ms)
            else:
                when = datetime.fromisoformat(str(value))
                micro = when.microsecond
                ms = (micro+999)//1000 if edge=='start' else micro//1000
                when = when.replace(microsecond=0) + timedelta(milliseconds=ms)
            return when.isoformat(timespec='milliseconds')
        if low > high:
            return {'role':role,'channel_ids':channel_ids,'has_common_window':False,
                    'coverage_mode':mode,'coverage':entries,'time_domain':domain}
        start, end = calendar(low,'start'), calendar(high,'end')
        return {'role':role,'channel_ids':channel_ids,'has_common_window':start<=end,
                'coverage_mode':mode,
                'min':start,'max':end,'coverage':entries,'time_domain':domain,
                'note':('可浏览各曲线的总时间边界；中间可能有无采样的空白区间'
                        if mode=='union' else '仅限制可选日期边界；中间采样缺口仍需查询时验证')}

def filter_options(db, *, role='unspecified', filters=None):
    """Read distinct hierarchy facets from SQLite using the selected ancestors."""
    role = _role(role)
    filters = filters or {}
    fields = ('namespace', 'satellite_instance', 'model', 'subsystem', 'device', 'variable')
    with open_database(db) as conn:
        segment_filter = _catalog_segment_filter(conn, role)
        result = {'role': role}
        for index, field in enumerate(fields):
            facet_filters = {key: filters[key] for key in fields[:index] if filters.get(key)}
            if filters.get('keyword'):
                facet_filters['keyword'] = filters['keyword']
            where, args = _filter_expr(facet_filters)
            rows = conn.execute(
                f'''SELECT DISTINCT c.{field} FROM v2_channels c
                    WHERE c.{field} IS NOT NULL AND c.{field}!=''
                      AND EXISTS (SELECT 1 FROM v2_segments g
                                  WHERE g.channel_id=c.id AND {segment_filter})''' + where +
                f' ORDER BY c.{field}', [role, *args]).fetchall()
            key = 'satellite_instances' if field == 'satellite_instance' else field + 's'
            result[key] = [row[0] for row in rows]
        return result


def _clock_contracts(conn, segment_ids, domain):
    """Prevent numeric clocks from different simulations sharing a time axis."""
    if domain != 'numeric_unspecified':
        return ('UTC', 'us') if domain == 'datetime_utc' else ('unknown_timezone', 'wall_clock')
    has_clock = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_segment_clock'").fetchone()
    clocks = {}
    if has_clock and segment_ids:
        slots = ','.join('?' for _ in segment_ids)
        clocks = {row[0]: (row[1], row[2]) for row in conn.execute(
            f'SELECT segment_id,reference,unit FROM v2_segment_clock WHERE segment_id IN ({slots})',segment_ids)}
    signatures = {clocks.get(sid, ('legacy_unknown_reference','unknown_unit')) for sid in segment_ids}
    if len(signatures) > 1:
        raise ValueError('参量存在不同数字时间基准或单位，禁止跨时间系统拼接')
    return next(iter(signatures), None)


def _time_expr(domain):
    return {'numeric_unspecified':'s.time_sort_num','datetime_utc':'s.time_utc_us',
            'datetime_naive':'s.time_sort_text'}[domain]


def _chart_base_sql(key: str, where: str) -> str:
    return f'''WITH base AS (
        SELECT s.timestamp_raw,s.value_num,s.label_int,s.source_ordinal,
               g.source_table,g.id AS segment_id,{key} AS time_key
        FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id
        WHERE {where}
    ), ranked AS (
        SELECT *,ROW_NUMBER() OVER (
            PARTITION BY time_key ORDER BY segment_id,source_ordinal
        ) AS dedup_rank
        FROM base
    ), unique_rows AS (
        SELECT timestamp_raw,value_num,label_int,source_ordinal,source_table,
               segment_id,time_key
        FROM ranked WHERE dedup_rank=1
    )'''


def _overview_points(conn, *, key: str, where: str, values: list, limit: int,
                     valid_sample_count: int):
    cte = _chart_base_sql(key, where)
    if valid_sample_count <= limit:
        sql = cte + '''
            SELECT timestamp_raw,value_num,label_int,source_ordinal,source_table,
                   segment_id,time_key
            FROM unique_rows WHERE value_num IS NOT NULL ORDER BY time_key'''
        return [dict(row) for row in conn.execute(sql, values)]

    if limit < 5:
        bucket_count = max(1, limit - 1)
        sql = cte + f''', valid AS (
            SELECT *,ROW_NUMBER() OVER (ORDER BY time_key) AS seq,
                   COUNT(*) OVER () AS total
            FROM unique_rows WHERE value_num IS NOT NULL
        ), bucketed AS (
            SELECT *,CAST((seq-1)*{bucket_count}/total AS INTEGER) AS bucket
            FROM valid
        ), ranked_bucket AS (
            SELECT *,ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY seq) AS bucket_rank
            FROM bucketed
        )
        SELECT timestamp_raw,value_num,label_int,source_ordinal,source_table,
               segment_id,time_key
        FROM ranked_bucket WHERE bucket_rank=1 OR seq=total ORDER BY time_key'''
        return [dict(row) for row in conn.execute(sql, values)]

    bucket_count = max(1, (limit - 2) // 3)
    sql = cte + f''', valid AS (
        SELECT *,ROW_NUMBER() OVER (ORDER BY time_key) AS seq,
               COUNT(*) OVER () AS total
        FROM unique_rows WHERE value_num IS NOT NULL
    ), bucketed AS (
        SELECT *,CAST((seq-1)*{bucket_count}/total AS INTEGER) AS bucket
        FROM valid
    ), bucket_positions AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY time_key) AS bucket_seq,
               COUNT(*) OVER (PARTITION BY bucket) AS bucket_total,
               MAX(CASE WHEN label_int=1 THEN 1 ELSE 0 END)
                   OVER (PARTITION BY bucket) AS bucket_has_fault
        FROM bucketed
    ), ranked_bucket AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY value_num ASC,time_key) AS min_rank,
               ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY value_num DESC,time_key) AS max_rank,
               ROW_NUMBER() OVER (PARTITION BY bucket
                   ORDER BY CASE WHEN label_int=1 THEN 0 ELSE 1 END,time_key) AS fault_rank
        FROM bucket_positions
    )
    SELECT timestamp_raw,value_num,label_int,source_ordinal,source_table,
           segment_id,time_key
    FROM ranked_bucket
    WHERE min_rank=1 OR max_rank=1 OR seq=1 OR seq=total
       OR (bucket_has_fault=1 AND label_int=1 AND fault_rank=1)
       OR (bucket_has_fault=0 AND bucket_seq=CAST((bucket_total+1)/2 AS INTEGER))
    ORDER BY time_key'''
    return [dict(row) for row in conn.execute(sql, values)]



_CURSOR_SNAPSHOT_CACHE = OrderedDict()
_CURSOR_SNAPSHOT_LOCK = threading.RLock()
_CURSOR_SNAPSHOT_CACHE_LIMIT = 4096


def _database_fingerprint(db) -> str:
    """Return a cheap change token for the SQLite database and its write sidecars."""
    path = Path(db).resolve()
    state = [str(path)]
    for candidate in (path, Path(str(path) + '-wal'), Path(str(path) + '-journal')):
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            state.append(None)
        else:
            state.append((stat.st_dev, stat.st_ino, stat.st_size,
                          stat.st_mtime_ns, stat.st_ctime_ns))
    encoded = json.dumps(state, separators=(',', ':'), allow_nan=False).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def _cursor_query_signature(db, *, channel_id, role, domain, start, end, segment_ids):
    return (str(Path(db).resolve()), channel_id, role, domain, start, end, tuple(segment_ids))


def _get_cursor_snapshot(cursor, *, data_signature, query_signature):
    if cursor is None:
        return None
    with _CURSOR_SNAPSHOT_LOCK:
        entry = _CURSOR_SNAPSHOT_CACHE.get(cursor)
        if entry is None:
            return None
        if (entry['data_signature'] != data_signature or
                entry['query_signature'] != query_signature):
            _CURSOR_SNAPSHOT_CACHE.pop(cursor, None)
            return None
        _CURSOR_SNAPSHOT_CACHE.move_to_end(cursor)
        return dict(entry['metrics'])


def _store_cursor_snapshot(cursor, *, data_signature, query_signature, metrics):
    with _CURSOR_SNAPSHOT_LOCK:
        _CURSOR_SNAPSHOT_CACHE[cursor] = {
            'data_signature': data_signature,
            'query_signature': query_signature,
            'metrics': dict(metrics),
        }
        _CURSOR_SNAPSHOT_CACHE.move_to_end(cursor)
        while len(_CURSOR_SNAPSHOT_CACHE) > _CURSOR_SNAPSHOT_CACHE_LIMIT:
            _CURSOR_SNAPSHOT_CACHE.popitem(last=False)


def _encode_sample_cursor(*, channel_id: int, role: str, domain: str,
                          start, end, frame_count: int, valid_sample_count: int,
                          matched_count: int, start_key, end_key,
                          time_key, frame_index: int, data_signature: str) -> str:
    payload = {
        'v': 2, 'channel_id': channel_id, 'role': role, 'domain': domain,
        'start': start, 'end': end, 'frame_count': frame_count,
        'valid_sample_count': valid_sample_count, 'matched_count': matched_count,
        'start_key': start_key, 'end_key': end_key,
        'last_key': time_key, 'frame_index': frame_index,
        'data_signature': data_signature,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _decode_sample_cursor(cursor, *, channel_id: int, role: str, domain: str,
                          start, end, frame_count: int, valid_sample_count: int,
                          matched_count: int, start_key, end_key, data_signature: str):
    if cursor is None:
        return {'last_key': None, 'frame_index': 0}
    if not isinstance(cursor, str) or not cursor or len(cursor) > 1024:
        raise ValueError('分页游标无效')
    try:
        padded = cursor + ('=' * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError('分页游标无效') from exc
    expected_keys = {'v', 'channel_id', 'role', 'domain', 'start', 'end',
                     'frame_count', 'valid_sample_count', 'matched_count',
                     'start_key', 'end_key', 'last_key', 'frame_index', 'data_signature'}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError('分页游标格式无效')
    if (payload.get('v') != 2 or payload.get('channel_id') != channel_id or
            payload.get('role') != role or payload.get('domain') != domain or
            payload.get('start') != start or payload.get('end') != end):
        raise ValueError('分页游标与当前参量、用途或时间窗口不匹配')
    if (payload.get('frame_count') != frame_count or
            payload.get('valid_sample_count') != valid_sample_count or
            payload.get('matched_count') != matched_count or
            payload.get('start_key') != start_key or payload.get('end_key') != end_key or
            payload.get('data_signature') != data_signature):
        raise ValueError('采样数据在分页期间发生变化，请重新加载明细')
    last_key = payload.get('last_key')
    if domain == 'numeric_unspecified':
        if isinstance(last_key, bool) or not isinstance(last_key, (int, float)) or not math.isfinite(float(last_key)):
            raise ValueError('分页游标时间键无效')
    elif domain == 'datetime_utc':
        if isinstance(last_key, bool) or not isinstance(last_key, int):
            raise ValueError('分页游标时间键无效')
    elif domain == 'datetime_naive':
        if not isinstance(last_key, str) or _timestamp(last_key, domain) != last_key:
            raise ValueError('分页游标时间键无效')
    else:
        raise ValueError('分页游标时间域无效')
    frame_index = payload.get('frame_index')
    if type(frame_index) is not int or not 1 <= frame_index <= frame_count:
        raise ValueError('分页游标帧序号无效')
    if start_key is None or end_key is None or last_key < start_key or last_key > end_key:
        raise ValueError('分页游标时间键超出当前数据范围')
    return {'last_key': last_key, 'frame_index': frame_index}

def query_charts(db, *, role, channel_ids, start=None, end=None, limit=None,
                 mode='overview', cursor=None):
    role = _role(role)
    if not isinstance(channel_ids, list) or not (1 <= len(channel_ids) <= MAX_CHANNELS) or len(set(map(str, channel_ids))) != len(channel_ids):
        raise ValueError('请选择 1～8 个不重复的参量')
    if mode not in ('overview', 'page'):
        raise ValueError('查询模式必须为 overview 或 page')
    if mode == 'overview' and cursor is not None:
        raise ValueError('overview 模式不接受分页游标')
    if limit is None:
        limit = 100 if mode == 'page' else 2000
    if mode == 'page':
        if len(channel_ids) != 1:
            raise ValueError('采样明细分页一次只能查询一个参量')
        if type(limit) is not int or not (1 <= limit <= 200):
            raise ValueError('采样明细 limit 必须在 1～200 之间')
    elif type(limit) is not int or not (1 <= limit <= MAX_POINTS):
        raise ValueError('limit 必须在 1～5000 之间')
    ids = []
    for value in channel_ids:
        if type(value) is not int or value <= 0:
            raise ValueError('channel_ids 只能包含正整数')
        ids.append(value)

    data_signature = _database_fingerprint(db) if mode == 'page' else None
    with open_database(db) as conn:
        output = []
        from scripts.server.telemetry_v2_governance import active_clause
        for cid in ids:
            channel = conn.execute('SELECT * FROM v2_channels WHERE id=?', (cid,)).fetchone()
            if not channel:
                raise ValueError(f'参量 {cid} 不存在')
            segs = conn.execute('''SELECT id,source_table,time_domain,time_start_raw,time_end_raw FROM v2_segments g
                WHERE channel_id=? AND kind='telemetry' AND usable_for_timeseries=1 AND ''' +
                _run_view(conn, role) + _policy(role) + active_clause(conn, 'g'), (cid, role)).fetchall()
            domains = {segment['time_domain'] for segment in segs}
            if len(domains) > 1:
                raise ValueError(f'{channel["variable"]} 存在多个时间域，不能直接混合；请先规范时基')
            if role == 'unspecified' and len({_unverified_split(segment['source_table']) for segment in segs}) > 1:
                raise ValueError(f'{channel["variable"]} 的未指定用途分片包含不同训练/测试/清洁来源；请先审核数据用途，禁止自动合并')
            if not segs:
                if mode == 'page' and cursor is not None:
                    raise ValueError('分页游标与当前参量、用途或时间窗口不匹配')
                output.append({
                    'channel': dict(channel), 'time_domain': None, 'points': [], 'source_tables': [],
                    'matched_count': 0, 'frame_count': 0, 'valid_sample_count': 0,
                    'plot_point_count': 0, 'truncated': False, 'duplicate_count': 0,
                    'coverage': {'start': None, 'end': None, 'start_key': None, 'end_key': None},
                    'window': {'start_key': None, 'end_key': None},
                    'page_row_count': 0 if mode == 'page' else None,
                    'page_has_more': False, 'next_cursor': None,
                })
                continue

            domain = next(iter(domains))
            clock = _clock_contracts(conn, [segment['id'] for segment in segs], domain)
            key = _time_expr(domain)
            left = _timestamp(start, domain)
            right = _timestamp(end, domain)
            if left is not None and right is not None and left > right:
                raise ValueError('开始时间不能晚于结束时间')
            conditions = [
                's.channel_id=?', _run_view(conn, role),
                'g.usable_for_timeseries=1', "g.kind='telemetry'", key + ' IS NOT NULL',
            ]
            values = [cid, role]
            if role in ('train', 'test'):
                conditions.append("g.role_provenance NOT IN ('name_hint_only','database_hint_only')")
            conditions.append('1=1' + active_clause(conn, 'g'))
            if left is not None:
                conditions.append(key + ' >= ?')
                values.append(left)
            if right is not None:
                conditions.append(key + ' <= ?')
                values.append(right)
            where = ' AND '.join(conditions)
            join = ' FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id WHERE ' + where

            query_signature = _cursor_query_signature(
                db, channel_id=cid, role=role, domain=domain, start=left, end=right,
                segment_ids=[int(segment['id']) for segment in segs]) if mode == 'page' else None
            cached_snapshot = (_get_cursor_snapshot(
                cursor, data_signature=data_signature, query_signature=query_signature)
                if mode == 'page' else None)
            if cached_snapshot is not None:
                matched_count = cached_snapshot['matched_count']
                frame_count = cached_snapshot['frame_count']
                valid_sample_count = cached_snapshot['valid_sample_count']
                start_key = cached_snapshot['start_key']
                end_key = cached_snapshot['end_key']
                duplicate_count = cached_snapshot['duplicate_count']
            else:
                if mode == 'page':
                    mismatched_domain = conn.execute(
                        'SELECT 1' + join + ' AND s.time_domain != ? LIMIT 1',
                        [*values, domain]).fetchone()
                    if mismatched_domain:
                        raise ValueError('采样记录时间域与来源分片不一致，已拒绝分页')
                matched_count = int(conn.execute('SELECT COUNT(*)' + join, values).fetchone()[0])
                stats = conn.execute(
                    f'''SELECT COUNT(DISTINCT {key}),
                               COUNT(DISTINCT CASE WHEN s.value_num IS NOT NULL THEN {key} END),
                               MIN({key}),MAX({key})''' + join, values).fetchone()
                frame_count, valid_sample_count = int(stats[0]), int(stats[1])
                start_key, end_key = stats[2], stats[3]
                duplicate_count = matched_count - frame_count

                conflict = conn.execute(
                    f'''SELECT {key} AS time_key,MIN(s.timestamp_raw) AS timestamp_raw
                        {join} GROUP BY {key}
                        HAVING COUNT(DISTINCT quote(s.value_num)||char(31)||quote(s.label_int))>1
                        LIMIT 1''', values).fetchone()
                if conflict:
                    raise ValueError(
                        f'{channel["variable"]} 时间戳 {conflict["timestamp_raw"]} 存在冲突采样，已拒绝绘图')

            page_has_more = False
            next_cursor = None
            page_row_count = None
            if mode == 'page':
                cursor_state = _decode_sample_cursor(
                    cursor, channel_id=cid, role=role, domain=domain, start=left, end=right,
                    frame_count=frame_count, valid_sample_count=valid_sample_count,
                    matched_count=matched_count, start_key=start_key, end_key=end_key,
                    data_signature=data_signature)
                if cursor_state['last_key'] is not None and cached_snapshot is None:
                    prefix_count = int(conn.execute(
                        f'SELECT COUNT(DISTINCT {key})' + join + ' AND ' + key + ' <= ?',
                        [*values, cursor_state['last_key']]).fetchone()[0])
                    if prefix_count != cursor_state['frame_index']:
                        raise ValueError('采样数据在分页期间发生变化，请重新加载明细')
                page_where = where + ' AND s.time_domain=?'
                page_values = [*values, domain]
                if cursor_state['last_key'] is not None:
                    page_where += ' AND ' + key + ' > ?'
                    page_values.append(cursor_state['last_key'])
                page_sql = f'''
                    WITH page_keys AS (
                        SELECT {key} AS time_key
                        FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id
                        WHERE {page_where}
                        GROUP BY {key}
                        ORDER BY {key}
                        LIMIT ?
                    ), page_candidates AS (
                        SELECT s.timestamp_raw,s.value_num,s.label_int,s.source_ordinal,
                               g.source_table,g.id AS segment_id,{key} AS time_key,
                               ROW_NUMBER() OVER (
                                   PARTITION BY {key} ORDER BY g.id,s.source_ordinal
                               ) AS dedup_rank
                        FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id
                        JOIN page_keys k ON {key}=k.time_key
                        WHERE {page_where}
                    ), numbered_page AS (
                        SELECT *,ROW_NUMBER() OVER (ORDER BY time_key) AS page_seq
                        FROM page_candidates WHERE dedup_rank=1
                    )
                    SELECT timestamp_raw,value_num,label_int,source_ordinal,source_table,
                           segment_id,time_key,page_seq+? AS frame_index
                    FROM numbered_page ORDER BY time_key LIMIT ?'''
                raw_points = [dict(row) for row in conn.execute(
                    page_sql, [*page_values, limit + 1, *page_values,
                               cursor_state['frame_index'], limit + 1]).fetchall()]
                page_has_more = len(raw_points) > limit
                raw_points = raw_points[:limit]
                page_row_count = len(raw_points)
                points = []
                for sample in raw_points:
                    time_key = sample['time_key']
                    points.append({
                        'timestamp': display_time(time_key) if domain == 'numeric_unspecified' else sample['timestamp_raw'],
                        'relative_seconds': time_key if domain == 'numeric_unspecified' else None,
                        'value': sample['value_num'], 'label': sample['label_int'],
                        'source_table': sample['source_table'],
                        'source_ordinal': sample['source_ordinal'],
                        'segment_id': sample['segment_id'], 'time_key': time_key,
                        'frame_index': sample['frame_index'],
                    })
                if page_has_more and points:
                    last = points[-1]
                    next_cursor = _encode_sample_cursor(
                        channel_id=cid, role=role, domain=domain, start=left, end=right,
                        frame_count=frame_count, valid_sample_count=valid_sample_count,
                        matched_count=matched_count, start_key=start_key, end_key=end_key,
                        time_key=last['time_key'], frame_index=last['frame_index'],
                        data_signature=data_signature)
                    _store_cursor_snapshot(
                        next_cursor, data_signature=data_signature,
                        query_signature=query_signature,
                        metrics={
                            'matched_count': matched_count, 'frame_count': frame_count,
                            'valid_sample_count': valid_sample_count,
                            'start_key': start_key, 'end_key': end_key,
                            'duplicate_count': duplicate_count,
                        })
                plot_point_count = 0
                truncated = False
            else:
                raw_points = _overview_points(
                    conn, key=key, where=where, values=values,
                    limit=limit, valid_sample_count=valid_sample_count)
                points = []
                for sample in raw_points:
                    time_key = sample['time_key']
                    points.append({
                        'timestamp': display_time(time_key) if domain == 'numeric_unspecified' else sample['timestamp_raw'],
                        'relative_seconds': time_key if domain == 'numeric_unspecified' else None,
                        'value': sample['value_num'], 'label': sample['label_int'],
                        'source_table': sample['source_table'],
                        'source_ordinal': sample['source_ordinal'],
                        'segment_id': sample['segment_id'], 'time_key': time_key,
                    })
                plot_point_count = len(points)
                truncated = valid_sample_count > limit
            source_tables = sorted({sample['source_table'] for sample in points})
            coverage = {
                'start': min((segment['time_start_raw'] for segment in segs if segment['time_start_raw']), default=None),
                'end': max((segment['time_end_raw'] for segment in segs if segment['time_end_raw']), default=None),
                'start_key': start_key, 'end_key': end_key,
            }
            output.append({
                'channel': dict(channel), 'time_domain': domain, 'time_reference': clock,
                'points': points, 'source_tables': source_tables, 'segment_count': len(segs),
                'display_time_base': '2026-01-01 00:00:00' if domain == 'numeric_unspecified' else None,
                'matched_count': matched_count, 'frame_count': frame_count,
                'valid_sample_count': valid_sample_count, 'plot_point_count': plot_point_count,
                'truncated': truncated, 'duplicate_count': duplicate_count,
                'coverage': coverage, 'window': {'start_key': start_key, 'end_key': end_key},
                'page_row_count': page_row_count, 'page_has_more': page_has_more,
                'next_cursor': next_cursor,
            })
        active = [item for item in output if item.get('points')]
        if len(active) > 1:
            domains = {item['time_domain'] for item in active}
            if len(domains) > 1:
                raise ValueError('所选多个参量处于不同时间域，不能在同一时间轴上比较')
            if 'numeric_unspecified' in domains and any(
                (item.get('time_reference') or ('', ''))[1] not in ('s', 'unknown_unit') for item in active
            ):
                raise ValueError('曲线的相对时钟单位不是秒，不能作跨卫星对比')
            if 'datetime_naive' in domains and len({item['channel']['namespace'] for item in active}) > 1:
                raise ValueError('无时区时间戳跨来源命名空间不能直接比较，请先明确 UTC 时基')
        if mode == 'page' and _database_fingerprint(db) != data_signature:
            raise ValueError('采样数据在分页期间发生变化，请重新加载明细')
        return {'role': role, 'series': output}
