"""Evidence-scoped fault association navigation for V2 telemetry.

Run truth is metadata, not a per-sensor failure verdict. Only exact, explicit
source-component mapping or channel-scoped events produce attribution. Numeric
pre/post contrast is a *candidate for inspection*, not a fault label, diagnosis,
or an algorithm evaluation metric. This service is strictly read-only.
"""
from __future__ import annotations

import math
from pathlib import Path

from scripts.server.telemetry_v2_access import list_channels, open_database
from scripts.server.telemetry_v2_governance import _has, active_clause

MIN_VALID_SAMPLES = 20
MEAN_SHIFT_SIGMA = 3.0
STD_RATIO = 3.0
MAX_ROWS = 500


def _stats(row):
    n0, n1 = int(row['normal_n'] or 0), int(row['fault_n'] or 0)
    data = {'normal_count': n0, 'fault_count': n1, 'shift_sigma': None,
            'std_ratio': None, 'normal_mean': row['normal_mean'],
            'fault_mean': row['fault_mean']}
    if n0 < MIN_VALID_SAMPLES or n1 < MIN_VALID_SAMPLES:
        return data, '正常/故障时间段有效样本不足，无法可靠比较'
    mu0, mu1 = row['normal_mean'], row['fault_mean']
    var0 = max(0.0, (row['normal_square'] or 0.0) - mu0 * mu0)
    var1 = max(0.0, (row['fault_square'] or 0.0) - mu1 * mu1)
    sd0, sd1 = math.sqrt(var0), math.sqrt(var1)
    # A flat baseline cannot support standardized deviation / volatility ratio.
    # Do not fabricate huge sigma scores by dividing by epsilon.
    if sd0 <= max(1e-9, abs(mu0) * 1e-9):
        return data, '故障前基线近似常值，变化标准化结果不可靠'
    shift = abs(mu1 - mu0) / sd0
    ratio = sd1 / sd0
    if not (math.isfinite(shift) and math.isfinite(ratio)):
        return data, '统计量不可用'
    data['shift_sigma'] = round(min(999.0, shift), 2)
    data['std_ratio'] = round(min(999.0, ratio), 2)
    if shift >= MEAN_SHIFT_SIGMA or ratio >= STD_RATIO:
        return data, '故障后均值或波动相对正常基线变化明显；需排除工况影响'
    return data, '已有可比较基线，未发现达到当前展示阈值的明显变化'


def _event_channels(conn, channel_ids, role):
    if not channel_ids or not _has(conn, 'v2_fault_events'):
        return {}
    found = {}
    for offset in range(0, len(channel_ids), 300):
        part = channel_ids[offset:offset + 300]
        slots = ','.join('?' for _ in part)
        for row in conn.execute(f'''SELECT channel_id,fault_type,start_raw,end_raw
          FROM v2_fault_events WHERE role=? AND status='active' AND label=1
          AND channel_id IN ({slots})''', [role, *part]):
            found.setdefault(row['channel_id'], []).append(dict(row))
    return found


def fault_associations(db: Path, *, model: str, role='test', fault_type=None,
                       namespace=None, limit=MAX_ROWS):
    """Rank all channels belonging to ONE selected run, with provenance.

    Does not infer health/failure of components, create events or rewrite labels.
    If an external dataset lacks a verified run annotation, explicit per-channel
    events can still be shown; automatic impact ranking remains unavailable.
    """
    if role != 'test':
        raise ValueError('故障关联分析仅使用测试用途，不分析训练数据')
    if not isinstance(model, str) or not model.strip() or len(model) > 150:
        raise ValueError('请先选择一个卫星型号，再分析故障关联参量')
    if namespace is not None and (not isinstance(namespace, str) or len(namespace) > 150):
        raise ValueError('来源命名空间格式错误')
    if fault_type is not None and (not isinstance(fault_type, str) or len(fault_type) > 120):
        raise ValueError('故障类型格式错误')
    if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
        raise ValueError('limit 必须在 1～500 之间')

    filters = {'model': model.strip()}
    if namespace:
        filters['namespace'] = namespace
    catalog = list_channels(db, role=role, filters=filters, limit=None)['items']
    if not catalog:
        return {'items': [], 'count': 0, 'model': model, 'events': [],
                'semantics': 'no_matching_test_channels'}
    channels = {ch['id']: ch for ch in catalog}
    if len(channels) > MAX_ROWS:
        raise ValueError('当前型号有超过500个参量，请先缩小数据来源')
    with open_database(db) as conn:
        if _has(conn, 'v2_run_annotations'):
            location_join = ('LEFT JOIN v2_run_fault_locations l ON l.source_id=a.source_id'
                             if _has(conn, 'v2_run_fault_locations') else '')
            fields = ('l.subsystem AS origin_subsystem,l.device AS origin_device'
                      if location_join else 'NULL AS origin_subsystem,NULL AS origin_device')
            query = f'''SELECT DISTINCT a.source_id,a.token,a.fault_type,a.fault_component,
                a.onset_s,a.fault_end_exclusive_s,{fields}
                FROM v2_run_annotations a JOIN v2_segments g ON g.source_id=a.source_id
                JOIN v2_channels c ON c.id=g.channel_id {location_join}
                WHERE c.model=? AND a.run_role='FAULT' AND a.split='validation'
                  AND a.metadata_not_model_features=1 AND g.role='simulation'
                  AND g.kind='telemetry' AND g.usable_for_timeseries=1
                  {"AND c.namespace=?" if namespace else ''}
                  {active_clause(conn, 'g')}
                ORDER BY a.source_id'''
            events = [dict(r) for r in conn.execute(query, (model, namespace) if namespace else (model,))]
        else:
            events = []
        if fault_type:
            events = [ev for ev in events if ev['fault_type'] == fault_type]
        if len(events) > 1:
            raise ValueError('该型号对应多个独立故障运行，不能混算。请先使用独立来源型号或运行标识')
        run = events[0] if events else None
        per_channel = _event_channels(conn, list(channels), role)
        rows = {}
        source_channels = set()
        if run is not None:
            source_channels = {r[0] for r in conn.execute('''SELECT DISTINCT channel_id FROM v2_segments g
                WHERE g.source_id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1''' +
                active_clause(conn, 'g'), (run['source_id'],))}
        if run is not None:
            # Relative seconds only. Different source files/timescales cannot be
            # silently joined even if their timestamps happen to match.
            source_id = run['source_id']
            clock_bad = (conn.execute('''SELECT 1 FROM v2_segments g
                LEFT JOIN v2_segment_clock c ON c.segment_id=g.id
                WHERE g.source_id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
                AND (g.time_domain!='numeric_unspecified' OR c.unit IS NULL OR c.unit!='s')
                LIMIT 1''', (source_id,)).fetchone()
                if _has(conn, 'v2_segment_clock') else True)
            if not clock_bad and run['onset_s'] is not None:
                for row in conn.execute('''SELECT g.channel_id,
                      SUM(CASE WHEN s.time_sort_num<a.onset_s AND s.value_num IS NOT NULL THEN 1 ELSE 0 END) normal_n,
                      SUM(CASE WHEN s.time_sort_num>=a.onset_s AND s.time_sort_num<a.fault_end_exclusive_s
                          AND s.value_num IS NOT NULL THEN 1 ELSE 0 END) fault_n,
                      AVG(CASE WHEN s.time_sort_num<a.onset_s THEN s.value_num END) normal_mean,
                      AVG(CASE WHEN s.time_sort_num>=a.onset_s AND s.time_sort_num<a.fault_end_exclusive_s
                          THEN s.value_num END) fault_mean,
                      AVG(CASE WHEN s.time_sort_num<a.onset_s THEN s.value_num*s.value_num END) normal_square,
                      AVG(CASE WHEN s.time_sort_num>=a.onset_s AND s.time_sort_num<a.fault_end_exclusive_s
                          THEN s.value_num*s.value_num END) fault_square
                    FROM v2_run_annotations a JOIN v2_segments g ON g.source_id=a.source_id
                      JOIN v2_samples s ON s.segment_id=g.id
                    WHERE a.source_id=? AND g.role='simulation' AND g.kind='telemetry'
                      AND g.usable_for_timeseries=1 AND s.time_sort_num IS NOT NULL
                      AND g.channel_id IN ({}) {} GROUP BY g.channel_id'''.format(
                          ','.join('?' for _ in channels), active_clause(conn, 'g')),
                    [source_id, *channels]):
                    rows[row['channel_id']] = row
        items = []
        for cid, channel in channels.items():
            matches = per_channel.get(cid, [])
            explicit = [ev for ev in matches if not fault_type or ev['fault_type'] == fault_type]
            annotated = bool(explicit)
            confirmed_origin = bool(run and cid in source_channels and run.get('origin_subsystem') and run.get('origin_device')
                                    and channel['subsystem'] == run['origin_subsystem']
                                    and channel['device'] == run['origin_device'])
            # No heuristic fault-origin detection from string similarity.
            stat, reason = _stats(rows[cid]) if cid in rows else (
                {'normal_count': 0, 'fault_count': 0, 'shift_sigma': None, 'std_ratio': None,
                 'normal_mean': None, 'fault_mean': None},
                '没有已核实的运行级故障时间窗口和秒级时钟，无法比较')
            if confirmed_origin:
                category, status = 'source', '明确故障源关联'
            elif annotated:
                category, status = 'source', '参量级事件已标注'
            elif stat['shift_sigma'] is not None and (
                    stat['shift_sigma'] >= MEAN_SHIFT_SIGMA or stat['std_ratio'] >= STD_RATIO):
                category, status = 'affected', '疑似受影响'
            elif stat['shift_sigma'] is not None:
                category, status = 'no_clear_change', '暂未发现明显变化'
            else:
                category, status = 'insufficient', '证据不足'
            note = ('已映射的故障源部件，仍不代表该部件全部测量通道都发生异常' if confirmed_origin else
                    '有明确参量事件标注，但不据此推断物理故障根因' if annotated else reason)
            items.append({'channel': channel, 'source_id': run['source_id'] if run and cid in source_channels else None,
                'category': category, 'status': status, 'origin_verified': confirmed_origin,
                'channel_event': annotated, 'fault_types': sorted({e['fault_type'] for e in explicit}),
                'first_fault_timestamp': str(run['onset_s']) if run and cid in source_channels and run['onset_s'] is not None else
                    (explicit[0]['start_raw'] if explicit else None),
                'last_fault_timestamp': str(run['fault_end_exclusive_s']) if run and cid in source_channels else
                    (explicit[0]['end_raw'] if explicit else None),
                'details': note, 'stats': stat})
    # Explicit origin/annotation first, then largest change, then uncertain items.
    order = {'source': 0, 'affected': 1, 'no_clear_change': 2, 'insufficient': 3}
    items.sort(key=lambda item: (order[item['category']],
        -(max(item['stats']['shift_sigma'] or 0, item['stats']['std_ratio'] or 0)),
        item['channel']['subsystem'], item['channel']['device'], item['channel']['variable']))
    return {'items': items[:limit], 'count': min(len(items), limit), 'total': len(items),
        'model': model, 'role': role, 'events': events, 'run_annotations_are_channel_faults': False,
        'categories': {key: sum(i['category'] == key for i in items) for key in order},
        'thresholds': {'min_valid_samples_per_window': MIN_VALID_SAMPLES,
                       'mean_shift_sigma': MEAN_SHIFT_SIGMA, 'std_increase_ratio': STD_RATIO},
        'semantics': 'read_only_evidence_ranking_not_fault_diagnosis',
        'explanation': '明确故障源只来自精确部件映射；变化明显仅提示需要检查，不能当作参量故障真值。'}
