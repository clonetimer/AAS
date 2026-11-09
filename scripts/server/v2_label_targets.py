"""Evidence-scoped supervised ground truth for V2 held-out run evaluation.

This module NEVER rewrites v2_samples.label_int, guesses a component from a
fault name, or treats statistical fault associations as ground truth.
Unknown means exclude from supervised evaluation (not normal). The normal
run-level labels remain available under the explicit run target.
"""
from __future__ import annotations

import math

SCOPES = {'run', 'component', 'parameter'}


def normalize_target(value):
    if value is None:
        return {'scope': 'run'}
    if not isinstance(value, dict) or value.get('scope') not in SCOPES:
        raise ValueError('评估目标必须为 run/component/parameter')
    scope = value['scope']
    fields = {'run': {'scope'}, 'component': {'scope', 'subsystem', 'device'},
              'parameter': {'scope', 'feature_key'}}[scope]
    if set(value) != fields:
        raise ValueError('评估目标字段不完整或包含额外属性')
    result = {'scope': scope}
    for field in fields - {'scope'}:
        item = value[field]
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= 160 or any(ord(c) < 32 for c in item):
            raise ValueError(f'评估目标 {field} 必须为有效文本')
        result[field] = item.strip()
    return result


def target_for_run(conn, run, target, features, semantics, segment_ids):
    """Return (classify(time_s), evidence), safely scoped to one source run.

    ``None`` is an unknown label and must not be converted to zero. Every
    non-run target must resolve to a feature in the evaluated feature matrix.
    """
    scope = target['scope']
    if scope == 'run':
        return None, {'source': 'v2_samples.label_int', 'scope': 'simulation_run'}

    if scope == 'component':
        component = (target['subsystem'], target['device'])
        if not any(tuple(s[:2]) == component for s in semantics):
            raise ValueError('所选评估部件没有对应的模型输入参量，拒绝错配指标')
        if run['data_role'] != 'simulation':
            raise ValueError('导入数据缺少经过验证的部件级故障位置；请补充显式事件，不可使用运行标签代替')
        if run['run_role'] == 'NORMAL':
            # A verified NORMAL run is a valid negative even if no fault-location
            # table is installed yet. No positive component label is inferred.
            return lambda t: 0, {'source': 'verified_normal_run', 'scope': 'component'}
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_fault_locations'").fetchone():
            raise ValueError('缺少故障部件位置凭据，不允许生成部件级故障标签')
        row = conn.execute('''SELECT subsystem,device,classification_basis FROM v2_run_fault_locations
                              WHERE source_id=?''', (run['source_id'],)).fetchone()
        onset = run.get('onset_s')
        if onset is None or not math.isfinite(float(onset)):
            raise ValueError('故障运行没有可信的发生时刻')
        same = bool(row and (row['subsystem'], row['device']) == component and row['classification_basis'])
        end = run.get('fault_end_exclusive_s')
        # A different component fault must not turn this component's post-onset
        # labels to 0; that condition is not proven normal or proven faulty.
        def classify(t):
            if t < onset:
                return 0
            if same and (end is None or t < float(end)):
                return 1
            return None
        return classify, {'source': 'v2_run_fault_locations' if same else 'unmatched_run_fault_location',
                          'scope': 'component', 'fault_source_match': same}

    key = target['feature_key']
    if key not in features:
        raise ValueError('参量级评估目标必须是本次输入的一个参量')
    index = features.index(key)
    # The event must explicitly apply to the exact source channel AND role.
    cid = conn.execute('SELECT channel_id FROM v2_segments WHERE id=?', (segment_ids[index],)).fetchone()[0]
    # Prefer explicitly reviewed, source+segment-scoped events. Unlike legacy
    # channel events these remain safe when multiple runs share a channel id.
    from scripts.server.v2_source_parameter_events import read_events
    clock = conn.execute('''SELECT reference,unit FROM v2_segment_clock WHERE segment_id=?''',
                         (segment_ids[index],)).fetchone()
    explicit=read_events(conn,run['source_id'],segment_ids[index],clock)
    if explicit is not None:
        intervals,details=explicit
        def scoped_classify(t):
            hits=[label for start,end,label in intervals if start<=t<end]
            return hits[0] if hits else None
        return scoped_classify,details
    # Legacy channel-scoped events lack source_id. Use them only when the
    # channel has exactly one source, never spread across different runs.
    owners=conn.execute('''SELECT COUNT(DISTINCT source_id) FROM v2_segments
        WHERE channel_id=? AND role=? AND kind='telemetry' AND usable_for_timeseries=1''',
        (cid,run['data_role'])).fetchone()[0]
    if owners != 1:
        raise ValueError('参量事件未明确绑定唯一来源运行，禁止跨运行传播标签')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_fault_events'").fetchone():
        return lambda t: None, {'source': 'missing_channel_events', 'scope': 'parameter'}
    rows = conn.execute('''SELECT e.start_num,e.end_num,e.label,e.time_domain,e.time_unit,e.time_reference
        FROM v2_fault_events e WHERE e.channel_id=? AND e.role=? AND e.status='active'
        ORDER BY e.start_num''', (cid,run['data_role'])).fetchall()
    clock = conn.execute('''SELECT reference,unit FROM v2_segment_clock WHERE segment_id=?''',
                         (segment_ids[index],)).fetchone()
    if clock is None:raise ValueError('目标参量缺少已确认时间基准')
    intervals = []
    for row in rows:
        if (row['time_domain'] != 'numeric_unspecified' or row['time_unit'] != 's'
                or row['time_reference'] != clock['reference']):
            raise ValueError('参量事件与选中运行的时间基准不匹配')
        if row['start_num'] is None or row['end_num'] is None:
            raise ValueError('参量事件缺少可比较的时间范围')
        intervals.append((row['start_num'], row['end_num'], row['label']))
    def classify(t):
        hits = [item[2] for item in intervals if item[0] <= t < item[1]]
        if len(set(hits)) > 1:raise ValueError('参量事件标签重叠冲突')
        return hits[0] if hits else None
    return classify, {'source': 'v2_fault_events', 'scope': 'parameter', 'event_count': len(intervals)}
