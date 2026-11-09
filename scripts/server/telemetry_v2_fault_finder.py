"""Read-only V2 fault-oriented *navigation*, not a diagnosis or label generator.

Use explicit per-channel events and labeled samples, then source-scoped run
annotations. A run's FAULT label must never imply that every channel failed.
The optional numeric contrast is descriptive only (not a detector threshold).
"""
from __future__ import annotations

import math
from pathlib import Path

from scripts.server.telemetry_v2_access import list_channels, open_database, _policy, _run_view
from scripts.server.telemetry_v2_governance import _has, active_clause


def _contrast(row):
    """Mean shift divided by healthy standard deviation; not a fault probability."""
    n0, n1 = row['normal_valid'], row['fault_valid']
    if n0 < 3 or n1 < 2 or row['healthy_mean'] is None or row['fault_mean'] is None:
        return None
    mean = row['healthy_mean']
    var = max(0., (row['healthy_sq_mean'] or 0.) - mean * mean)
    # Constant healthy streams cannot establish reliable statistical variation.
    if var <= max(abs(mean) * 1e-12, 1e-12) ** 2:
        return None
    score = abs(row['fault_mean'] - mean) / math.sqrt(var)
    return round(min(score, 999.), 2) if math.isfinite(score) else None


def fault_index(db: Path, *, role='test', filters=None, limit=500, fault_type=None):
    """Return tagged candidates in an authorized role, without mutating the DB.

    Data sources: explicit fault events, sample labels, and for validation only,
    simulation-run annotations. Does NOT train a detector or infer new labels.
    """
    if role not in ('test', 'simulation', 'raw', 'simulated_fault'):
        raise ValueError('故障速查仅支持测试、仿真或原始数据用途，不扫描训练样本')
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('limit 必须在 1～500 之间')
    if fault_type is not None and (not isinstance(fault_type, str) or not 1 <= len(fault_type) <= 120):
        raise ValueError('故障类型必须是有效字符串')
    filters = filters or {}
    if not isinstance(filters, dict) or set(filters) - {'model', 'namespace', 'subsystem', 'device', 'keyword'}:
        raise ValueError('筛选字段不合法')
    if any(not isinstance(value, str) or len(value) > 150 for value in filters.values()):
        raise ValueError('筛选字段格式错误或过长')
    # Identity is the existing logical channel, not a physical table name.
    catalog = list_channels(db, role=role, filters=filters, limit=None)['items']
    if not catalog:
        return {'items': [], 'count': 0, 'total_candidates': 0, 'truncated': False,
                'fault_types': [], 'role': role, 'semantics': 'evidence_not_diagnosis'}
    catalog_by_id = {item['id']: item for item in catalog}
    ids = list(catalog_by_id)
    result = {cid: {'channel': catalog_by_id[cid], 'fault_samples': 0,
                    'normal_samples': 0, 'invalid_fault_samples': 0,
                    'shift_sigma': None, 'evidence': [], 'fault_types': [],
                    'fault_origins': [], 'is_fault_origin': False,
                    'first_fault_timestamp': None, 'last_fault_timestamp': None}
              for cid in ids}
    with open_database(db) as conn:
        # One grouped scan over authorized active segments, never over hinted
        # train/test role partitions. Parameterize even the IN-list elements.
        for offset in range(0, len(ids), 250):
            group = ids[offset:offset+250]
            places = ','.join('?' for _ in group)
            rows = conn.execute(f'''SELECT g.channel_id AS cid,
                SUM(CASE WHEN s.label_int=1 AND s.value_num IS NOT NULL THEN 1 ELSE 0 END) AS fault_valid,
                SUM(CASE WHEN s.label_int=0 AND s.value_num IS NOT NULL THEN 1 ELSE 0 END) AS normal_valid,
                SUM(CASE WHEN s.label_int=1 AND s.value_num IS NULL THEN 1 ELSE 0 END) AS fault_invalid,
                AVG(CASE WHEN s.label_int=0 THEN s.value_num END) AS healthy_mean,
                AVG(CASE WHEN s.label_int=1 THEN s.value_num END) AS fault_mean,
                AVG(CASE WHEN s.label_int=0 THEN s.value_num*s.value_num END) AS healthy_sq_mean,
                MIN(CASE WHEN s.label_int=1 THEN s.timestamp_raw END) AS first_fault,
                MAX(CASE WHEN s.label_int=1 THEN s.timestamp_raw END) AS last_fault
              FROM v2_segments g JOIN v2_samples s ON s.segment_id=g.id
              WHERE g.channel_id IN ({places}) AND g.kind='telemetry' AND g.usable_for_timeseries=1
                AND {_run_view(conn, role)} {_policy(role)} {active_clause(conn, 'g')}
              GROUP BY g.channel_id''', [*group, role]).fetchall()
            for row in rows:
                entry = result[row['cid']]
                entry['fault_samples'] = row['fault_valid'] or 0
                entry['normal_samples'] = row['normal_valid'] or 0
                entry['invalid_fault_samples'] = row['fault_invalid'] or 0
                entry['first_fault_timestamp'] = row['first_fault']
                entry['last_fault_timestamp'] = row['last_fault']
                entry['shift_sigma'] = _contrast(row)
                if entry['fault_samples'] or entry['invalid_fault_samples']:
                    entry['evidence'].append('sample_label')

        if _has(conn, 'v2_fault_events'):
            # Explicit events in the requested role only. A simulation event
            # lacks source_id, and cannot be attributed to a validation run.
            events = conn.execute('''SELECT e.channel_id,e.fault_type,e.start_raw,e.end_raw
                FROM v2_fault_events e WHERE e.role=? AND e.status='active' AND e.label=1''', (role,))
            for event in events:
                entry = result.get(event['channel_id'])
                if entry is None:
                    continue
                if 'channel_event' not in entry['evidence']:
                    entry['evidence'].append('channel_event')
                if event['fault_type'] not in entry['fault_types']:
                    entry['fault_types'].append(event['fault_type'])
                if entry['first_fault_timestamp'] is None:
                    entry['first_fault_timestamp'] = event['start_raw']
                    entry['last_fault_timestamp'] = event['end_raw']

        if _has(conn, 'v2_run_annotations') and role in ('test', 'simulation'):
            join_location = ("LEFT JOIN v2_run_fault_locations l ON l.source_id=a.source_id"
                if _has(conn, 'v2_run_fault_locations') else '')
            origin_fields = ('l.subsystem AS origin_subsystem,l.device AS origin_device'
                if join_location else 'NULL AS origin_subsystem,NULL AS origin_device')
            required = "a.split='validation'" if role == 'test' else "1=1"
            rows = conn.execute(f'''SELECT DISTINCT g.channel_id,a.source_id,a.fault_type,
                    a.fault_component,a.onset_s,a.fault_end_exclusive_s,
                    {origin_fields}
                FROM v2_run_annotations a JOIN v2_segments g ON g.source_id=a.source_id
                {join_location}
                WHERE g.kind='telemetry' AND g.usable_for_timeseries=1
                AND g.role='simulation' AND a.run_role='FAULT' AND a.metadata_not_model_features=1
                AND {required} {active_clause(conn, 'g')}''').fetchall()
            for row in rows:
                entry = result.get(row['channel_id'])
                if entry is None:
                    continue
                if 'run_annotation' not in entry['evidence']:
                    entry['evidence'].append('run_annotation')
                if row['fault_type'] and row['fault_type'] not in entry['fault_types']:
                    entry['fault_types'].append(row['fault_type'])
                origin = {'source_id': row['source_id'], 'fault_component': row['fault_component'],
                          'subsystem': row['origin_subsystem'], 'device': row['origin_device']}
                entry['fault_origins'].append(origin)
                if (row['origin_subsystem'] and row['origin_device'] and
                    row['origin_subsystem'] == entry['channel']['subsystem'] and
                    row['origin_device'] == entry['channel']['device']):
                    entry['is_fault_origin'] = True
                if entry['first_fault_timestamp'] is None and row['onset_s'] is not None:
                    entry['first_fault_timestamp'] = str(row['onset_s'])
                    entry['last_fault_timestamp'] = str(row['fault_end_exclusive_s'])

    candidates = [item for item in result.values() if item['evidence']]
    # If anomaly contrast is unavailable (e.g. all samples from a faulty run),
    # never replace it with a fabricated positive score.
    def priority(item):
        return (int('channel_event' in item['evidence']), int(item['is_fault_origin']),
                item['shift_sigma'] if item['shift_sigma'] is not None else -1,
                item['fault_samples'])
    candidates.sort(key=priority, reverse=True)
    types = sorted({t for item in candidates for t in item['fault_types']})
    if fault_type:
        candidates = [item for item in candidates if fault_type in item['fault_types']]
    for entry in candidates:
        entry['fault_types'].sort()
        entry['fault_origins'] = entry['fault_origins'][:16]
    return {'items': candidates[:limit], 'count': min(len(candidates), limit),
            'total_candidates': len(candidates), 'truncated': len(candidates) > limit,
            'fault_types': types, 'role': role, 'semantics': 'evidence_not_diagnosis',
            'explanation': '事件标注、运行级标签及采样点故障标签均为检索线索；变化倍数不是故障概率，不能作为最终诊断。'}
