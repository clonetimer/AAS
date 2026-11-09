"""V2-only train/evaluation source contract: logical channels, never table names.

Immutable input identity is namespace + channel metadata + time domain/clock;
roles are authoritative (metadata-declared or explicit V2 import), never guessed.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from scripts.server.telemetry_v2_access import open_database, _timestamp, _time_expr, _clock_contracts

MAX_FEATURES = 8
MAX_PER_CHANNEL = 200_000
MAX_ALIGNED = 200_000
ALLOWED_METHODS = ('exact', 'nearest', 'previous')


def _channels(ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_FEATURES or any(type(x) is not int or x <= 0 for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('必须选择 1～8 个不重复的 V2 逻辑参量 ID')
    return list(ids)


def _args(ids, role, method, tolerance_seconds, start, end):
    ids = _channels(ids)
    if role not in ('train', 'test'):
        raise ValueError('训练/评估必须明确指定 train 或 test 数据用途')
    if method not in ALLOWED_METHODS:
        raise ValueError('对齐方式仅支持 exact / nearest / previous')
    if isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, (int, float)) or not math.isfinite(tolerance_seconds) or not 0 <= tolerance_seconds <= 3600:
        raise ValueError('时间容差必须介于 0～3600 秒')
    if method == 'exact' and tolerance_seconds != 0:
        raise ValueError('精确对齐不能配置非零容差')
    if (start is not None and not isinstance(start, str)) or (end is not None and not isinstance(end, str)):
        raise ValueError('时间范围必须是文本')
    return ids


def _read_channel(conn, channel_id, role, start, end):
    chan = conn.execute('SELECT * FROM v2_channels WHERE id=?', (channel_id,)).fetchone()
    if chan is None:
        raise ValueError(f'参量 {channel_id} 不存在')
    from scripts.server.telemetry_v2_governance import active_clause
    segs = conn.execute('''SELECT id,source_id,time_domain,source_table,source_row_count,time_start_raw,time_end_raw
        FROM v2_segments WHERE kind='telemetry' AND usable_for_timeseries=1
          AND channel_id=? AND role=? AND role_provenance NOT IN ('name_hint_only','database_hint_only')
        '''+active_clause(conn,'v2_segments')+' ORDER BY id', (channel_id, role)).fetchall()
    if not segs:
        raise ValueError(f'参量 {channel_id} 没有已确认用途的 {role} 数据分片')
    domains = {s['time_domain'] for s in segs}
    if len(domains) != 1 or next(iter(domains)) not in ('datetime_utc', 'datetime_naive','numeric_unspecified'):
        raise ValueError(f'参量 {channel_id} 包含未知或混合时间域，无法构造训练/评估样本')
    domain = next(iter(domains))
    clock = _clock_contracts(conn, [s['id'] for s in segs], domain)
    left, right = _timestamp(start, domain), _timestamp(end, domain)
    if left is not None and right is not None and left > right:
        raise ValueError('时间范围起点晚于终点')
    key = _time_expr(domain)
    placeholders = ','.join('?' for _ in segs)
    where = [f's.segment_id IN ({placeholders})']
    params = [s['id'] for s in segs]
    if left is not None:
        where.append(key+' >= ?'); params.append(left)
    if right is not None:
        where.append(key+' <= ?'); params.append(right)
    sql = f'''SELECT {key} AS moment,s.timestamp_raw,s.value_num,s.label_int,s.segment_id,s.source_ordinal
               FROM v2_samples s WHERE {' AND '.join(where)}
               ORDER BY {key},s.segment_id,s.source_ordinal LIMIT ?'''
    rows = conn.execute(sql, [*params, MAX_PER_CHANNEL+1]).fetchall()
    if len(rows) > MAX_PER_CHANNEL:
        raise ValueError(f'参量 {channel_id} 超过 {MAX_PER_CHANNEL} 条采样，请缩小时间范围，禁止隐式截断')
    clean = []
    invalid_count = 0
    for row in rows:
        r = dict(row)
        if r['moment'] is None or r['label_int'] not in (0, 1):
            raise ValueError(f'参量 {channel_id} 存在无效时间或真实标签，禁止用于训练评估')
        if r['value_num'] is None:
            invalid_count += 1
            continue  # quality-marked NULL remains stored, never silently filled
        if not math.isfinite(r['value_num']):
            invalid_count += 1
            continue
        if role == 'train' and r['label_int'] != 0:
            raise ValueError(f'训练参量 {channel_id} 发现故障标签，拒绝作为正常训练集')
        if clean and r['moment'] == clean[-1]['moment']:
            if (r['value_num'],r['label_int']) != (clean[-1]['value_num'],clean[-1]['label_int']):
                raise ValueError(f'参量 {channel_id} 在时间戳 {r["timestamp_raw"]} 存在冲突采样')
            continue
        clean.append(r)
    if not clean:
        raise ValueError(f'参量 {channel_id} 在所选时间段没有有效样本')
    identity = {k: chan[k] for k in ('namespace','satellite_instance','model','subsystem','device','variable','unit')}
    sample_digest = hashlib.sha256(json.dumps(
        [[r['moment'],r['value_num'],r['label_int'],r['segment_id'],r['source_ordinal']] for r in clean],
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    segment_info = [{k: s[k] for k in ('id','source_id','source_row_count')} for s in segs]
    signature = hashlib.sha256(json.dumps({'identity':identity,'role':role,'clock':clock,
        'time_domain':domain,'segments':segment_info},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'channel_id':channel_id, 'identity':identity, 'domain':domain, 'clock':clock,
            'segment_ids':[s['id'] for s in segs], 'signature':signature, 'sample_sha256':sample_digest,
            'quality':{'total':len(rows),'valid_samples':len(clean),'invalid_samples':invalid_count,
                       'valid_rate':round(len(clean)/len(rows),6) if rows else 0},
            'rows':clean, 'all_rows':[dict(r) for r in rows]}


def _select(series, keys, moment, method, tolerance):
    if method == 'exact':
        idx = bisect_left(keys, moment)
        return series[idx] if idx < len(keys) and keys[idx] == moment else None
    if method == 'previous':
        idx = bisect_right(keys, moment) - 1
        return series[idx] if idx >= 0 and moment-keys[idx] <= tolerance else None
    i = bisect_left(keys, moment)
    candidates = [j for j in (i-1, i) if 0 <= j < len(keys)]
    if not candidates: return None
    j = min(candidates, key=lambda v:(abs(keys[v]-moment), keys[v]))
    return series[j] if abs(keys[j]-moment) <= tolerance else None


def load_matrix(db_path: Path, *, channel_ids, role, method='exact', tolerance_seconds=0,
                start=None, end=None, expected=None):
    ids = _args(channel_ids, role, method, tolerance_seconds, start, end)
    with open_database(db_path) as conn:
        channels = [_read_channel(conn, x, role, start, end) for x in ids]
    if expected is not None:
        if not isinstance(expected, list) or len(expected) != len(channels):
            raise ValueError('模型特征契约缺失或数量不匹配')
        for current, original in zip(channels, expected):
            if current['identity'] != original.get('identity') or current['domain'] != original.get('domain') or list(current['clock'] or []) != list(original.get('clock') or []):
                raise ValueError('评估参量身份或时基不符合模型训练契约')
            if role == 'train' and (current['signature'] != original.get('signature') or
                                    current['sample_sha256'] != original.get('sample_sha256')):
                raise ValueError('训练任务提交后数据源发生变化，禁止使用过时的训练契约')
    domain = channels[0]['domain']; clock = channels[0]['clock']
    if any(x['domain'] != domain or x['clock'] != clock for x in channels):
        raise ValueError('参量时间域/时基不同，不允许形成同一特征矩阵')
    if domain == 'datetime_naive' and len({x['identity']['namespace'] for x in channels})>1:
        raise ValueError('缺少时区的不同来源不能做时间对齐')
    if domain == 'numeric_unspecified' and clock == ('legacy_unknown_reference','unknown_unit'):
        raise ValueError('数字时间基准与单位未知，禁止作为训练或评估输入')
    scale = 1_000_000 if domain in ('datetime_utc', 'datetime_naive') else 1.0
    if domain == 'numeric_unspecified':
        unit = clock[1]
        if unit not in ('s','ms','us','ns'):
            if len(channels)>1: raise ValueError('数值时间单位未知')
        else: scale={'s':1,'ms':1000,'us':1_000_000,'ns':1_000_000_000}[unit]
    tol = tolerance_seconds * scale
    rows = []
    # Anchor first channel; match each additional channel by real timestamps, not row order.
    # NULL observations remain as barriers in matching; previous/nearest must
    # not silently leap across a fourth consecutive unfilled timestamp.
    tails = [c['all_rows'] for c in channels[1:]]
    # Naive timestamps remain in their own unzoned clock. Conversion below is
    # only for computing time deltas, never for assuming a UTC epoch.
    def distance_key(moment):
        if domain != 'datetime_naive':
            return moment
        naive = datetime.fromisoformat(moment)
        day = naive.toordinal()
        return ((day * 86400 + naive.hour * 3600 + naive.minute * 60 + naive.second)
                * 1_000_000 + naive.microsecond)
    tail_keys = [[distance_key(r['moment']) for r in seq] for seq in tails]
    for anchor in channels[0]['rows']:
        combined = [anchor]
        for seq, keys in zip(tails,tail_keys):
            match = _select(seq, keys, distance_key(anchor['moment']), method, tol)
            if match is None or match['value_num'] is None or not math.isfinite(match['value_num']): break
            combined.append(match)
        if len(combined) != len(channels): continue
        source_labels={item['label_int'] for item in combined}
        if len(source_labels)!=1:
            raise ValueError('所选参量标签不一致，禁止以 max/OR 合并为一个伪故障真值；请核对各参量标签语义')
        label=next(iter(source_labels))
        row = {'timestamp': anchor['timestamp_raw'], 'label':label,
               'value':anchor['value_num']}
        for i, item in enumerate(combined[1:],2):
            row[f'source_{i}'] = item['value_num']
        rows.append(row)
        if len(rows)>MAX_ALIGNED:raise ValueError('对齐样本超过安全上限，请缩小时间范围')
    if not rows: raise ValueError('所选参量与时间范围内没有可对齐的采样')
    labels={r['label'] for r in rows}
    if role == 'test' and labels != {0,1}:
        raise ValueError('最终对齐的测试样本必须同时包含正常和故障真实标签')
    feature_names=['value']+[f'source_{i}' for i in range(2,len(channels)+1)]
    metadata={'schema':'telemetry-v2', 'channel_ids':ids,'role':role,'method':method,
              'tolerance_seconds':tolerance_seconds,'feature_columns':feature_names,
              'sources':[{k:v for k,v in c.items() if k not in ('rows','all_rows')} for c in channels],
              'aligned_count':len(rows),'label_policy':'same_scope_consensus_only','timeline':'first_channel',
              'time_window':{'start':start,'end':end},
              'sample_check':{'aligned_valid_samples':len(rows),
                  'per_channel':[{ 'channel_id':c['channel_id'],**c['quality']} for c in channels],
                  'accepted':bool(rows) and all(math.isfinite(r[k]) for r in rows for k in feature_names)}}
    return rows, metadata


def validate_training_selection(path, payload):
    if not isinstance(payload, dict):raise ValueError('训练参数必须是对象')
    allowed={'channel_ids','method','tolerance_seconds','start','end','algorithm','algorithm_params','normalization_method','train_ratio','training_strategy'}
    unknown=set(payload)-allowed
    if unknown:raise ValueError('V2 训练不接受旧数据表参数：'+', '.join(sorted(unknown)))
    ids=_channels(payload.get('channel_ids'))
    ratio=payload.get('train_ratio',0.8)
    if isinstance(ratio,bool) or not isinstance(ratio,(int,float)) or not 0.5<=ratio<1:
        raise ValueError('训练/验证划分比例必须在 0.5～1 之间（不含 1）')
    rows, contract=load_matrix(path,channel_ids=ids,role='train',method=payload.get('method','exact'),
        tolerance_seconds=payload.get('tolerance_seconds',0),start=payload.get('start'),end=payload.get('end'))
    split=int(len(rows)*ratio)
    if split<2 or len(rows)-split<1:raise ValueError('样本不足以进行按时间的训练/验证划分')
    return contract
