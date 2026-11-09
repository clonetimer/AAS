"""V2-only dataset governance and fault-event annotations.

No legacy table routes, sample mutation, inferred fault labels or physical DELETE.
The additive schema installs only through an explicit administrator POST; reads
remain compatible with the original rebuilt schema. All writes are atomic and
leave an immutable audit trail of the transition request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

from scripts.server.telemetry_v2_access import open_database, _timestamp, _clock_contracts

SCHEMA = '''
CREATE TABLE IF NOT EXISTS v2_segment_governance(
 segment_id INTEGER PRIMARY KEY REFERENCES v2_segments(id),
 active INTEGER NOT NULL CHECK(active IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL,
 updated_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_fault_events(
 id INTEGER PRIMARY KEY,
 channel_id INTEGER NOT NULL REFERENCES v2_channels(id),
 role TEXT NOT NULL,
 time_domain TEXT NOT NULL,
 time_reference TEXT NOT NULL,
 time_unit TEXT NOT NULL,
 start_raw TEXT NOT NULL,
 end_raw TEXT NOT NULL,
 start_num REAL,
 end_num REAL,
 start_text TEXT,
 end_text TEXT,
 label INTEGER NOT NULL CHECK(label IN (0,1)),
 fault_type TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retracted')),
 revision INTEGER NOT NULL DEFAULT 0,
 created_utc TEXT NOT NULL,
 updated_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_events_channel ON v2_fault_events(channel_id,role,status);
CREATE TABLE IF NOT EXISTS v2_governance_audit(
 id INTEGER PRIMARY KEY,
 entity TEXT NOT NULL,
 entity_id INTEGER NOT NULL,
 actor TEXT NOT NULL,
 action TEXT NOT NULL,
 old_json TEXT NOT NULL,
 new_json TEXT NOT NULL,
 reason TEXT NOT NULL,
 recorded_utc TEXT NOT NULL
);
'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _has(conn, name):
    return conn.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone() is not None


def active_clause(conn, alias='g'):
    """Return a filter valid for both old and governed V2 databases."""
    if not _has(conn, 'v2_segment_governance'):
        return ''
    return f''' AND NOT EXISTS(SELECT 1 FROM v2_segment_governance gov
                  WHERE gov.segment_id={alias}.id AND gov.active=0)'''


def _validate(db):
    with open_database(db):
        pass


def _check_text(value, name, maximum=240, *, required=True):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f'{name} 必须为不含控制字符的文本（最长 {maximum} 字符）')
    return value.strip()


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} 必须是正整数')
    return value


def _audit(conn, kind, item_id, action, old, new, reason, actor):
    conn.execute('''INSERT INTO v2_governance_audit
      (entity,entity_id,actor,action,old_json,new_json,reason,recorded_utc)
      VALUES (?,?,?,?,?,?,?,?)''', (kind, item_id, actor, action, json.dumps(old,ensure_ascii=False,sort_keys=True),
      json.dumps(new,ensure_ascii=False,sort_keys=True), reason, _now()))


def _write(db, work):
    db = Path(db).resolve()
    _validate(db)
    with sqlite3.connect(db, timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != '2':
            raise ValueError('仅允许对有效的 V2 数据库执行治理操作')
        for statement in SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        result = work(conn)
        conn.commit()
        return result


def _governed_segments(conn, *, channel_id=None, role=None, limit=200):
    filters=['g.kind=\'telemetry\'','g.usable_for_timeseries=1']
    args=[]
    if channel_id is not None:
        filters.append('g.channel_id=?');args.append(_id(channel_id,'channel_id'))
    if role is not None:
        if role not in ('raw','train','test','simulation','processed','simulated_fault','unspecified'):
            raise ValueError('无效用途')
        filters.append('g.role=?');args.append(role)
    if type(limit) is not int or not (1<=limit<=500): raise ValueError('limit 必须为 1～500')
    extension = ('COALESCE(gov.active,1) AS active, COALESCE(gov.revision,0) AS revision, '
                 "COALESCE(gov.reason,'') AS governance_reason") if _has(conn,'v2_segment_governance') else \
                "1 AS active, 0 AS revision, '' AS governance_reason"
    join = 'LEFT JOIN v2_segment_governance gov ON gov.segment_id=g.id' if _has(conn,'v2_segment_governance') else ''
    sql=f'''SELECT g.id AS segment_id,g.channel_id,g.role,g.role_provenance,g.time_domain,
                  g.source_row_count,g.time_start_raw,g.time_end_raw,g.source_table,
                  c.model,c.subsystem,c.device,c.variable,c.namespace,{extension}
              FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id {join}
              WHERE {' AND '.join(filters)} ORDER BY g.id DESC LIMIT ?'''
    return [dict(r) for r in conn.execute(sql,[*args,limit])]


def list_segments(db, *, channel_id=None, role=None, limit=200):
    with open_database(db) as conn:
        return {'items':_governed_segments(conn,channel_id=channel_id,role=role,limit=limit)}


def set_segment_status(db, payload, *, actor='internal'):
    if not isinstance(payload,dict) or payload.get('confirm') is not True:
        raise ValueError('分片状态修改需要 confirm=true')
    seg_id=_id(payload.get('segment_id'),'segment_id')
    if type(payload.get('active')) is not bool:
        raise ValueError('active 必须为布尔值')
    active=int(payload['active'])
    revision=payload.get('expected_revision')
    if type(revision) is not int or revision<0: raise ValueError('必须提交 expected_revision')
    reason=_check_text(payload.get('reason'), '治理原因',500)
    def work(conn):
        old=conn.execute('''SELECT g.id,g.role,g.channel_id,g.kind,g.usable_for_timeseries,
             COALESCE(gov.active,1) AS active,COALESCE(gov.revision,0) AS revision
             FROM v2_segments g LEFT JOIN v2_segment_governance gov ON gov.segment_id=g.id
             WHERE g.id=?''',(seg_id,)).fetchone()
        if not old or old['kind']!='telemetry' or old['usable_for_timeseries']!=1:
            raise ValueError('只允许治理 V2 标准遥测分片')
        if old['revision']!=revision:raise ValueError('分片版本已变化，请刷新再操作')
        if old['active']==active:raise ValueError('分片已经处于请求状态，不重复操作')
        if active:
            # A previously quarantined part may have been replaced in the
            # meantime. Restoring must not revive conflicting values/timebases.
            others=conn.execute('''SELECT g.id,g.time_domain FROM v2_segments g WHERE g.channel_id=?
                AND g.role=? AND g.id!=? AND g.kind='telemetry' AND g.usable_for_timeseries=1'''+active_clause(conn,'g'),
                (old['channel_id'],old['role'],seg_id)).fetchall()
            domain=conn.execute('SELECT time_domain FROM v2_segments WHERE id=?',(seg_id,)).fetchone()[0]
            if others and any(row['time_domain']!=domain for row in others):
                raise ValueError('恢复将造成时间域混合，拒绝执行')
            if others and domain=='numeric_unspecified':
                _clock_contracts(conn,[seg_id]+[r['id'] for r in others],domain)
            if others and domain in ('datetime_utc','numeric_unspecified','datetime_naive'):
                field={'datetime_utc':'time_utc_us','datetime_naive':'time_sort_text',
                       'numeric_unspecified':'time_sort_num'}[domain]
                conflict=conn.execute(f'''SELECT 1 FROM v2_samples a JOIN v2_samples b
                      ON b.channel_id=a.channel_id AND b.time_domain=a.time_domain
                      AND b.{field}=a.{field} AND b.segment_id!=a.segment_id
                    JOIN v2_segments g ON g.id=b.segment_id
                    WHERE a.segment_id=? AND g.role=? AND g.kind='telemetry'
                      AND g.usable_for_timeseries=1 AND
                      (a.value_num IS NOT b.value_num OR a.label_int IS NOT b.label_int)'''+active_clause(conn,'g')+' LIMIT 1',
                    (seg_id,old['role'])).fetchone()
                if conflict:raise ValueError('恢复会重新引入冲突时间戳，拒绝操作；请保持该分片隔离')
        newrev=revision+1
        conn.execute('''INSERT INTO v2_segment_governance(segment_id,active,revision,reason,updated_utc)
           VALUES(?,?,?,?,?) ON CONFLICT(segment_id) DO UPDATE SET active=excluded.active,
           revision=excluded.revision,reason=excluded.reason,updated_utc=excluded.updated_utc''',
           (seg_id,active,newrev,reason,_now()))
        _audit(conn,'segment',seg_id,'restore' if active else 'quarantine',
               {'active':old['active'],'revision':revision},
               {'active':active,'revision':newrev},reason,actor)
        return {'segment_id':seg_id,'active':bool(active),'revision':newrev,'status':'updated'}
    return _write(db,work)


def _get_scope(conn, cid, role):
    if role not in ('raw','test','simulation','processed','simulated_fault','train'):
        raise ValueError('仅允许为用途明确的逻辑参量创建事件')
    if not conn.execute('SELECT 1 FROM v2_channels WHERE id=?',(cid,)).fetchone():
        raise ValueError('参量不存在')
    active=active_clause(conn,'g')
    segments=conn.execute('''SELECT g.id,g.time_domain,g.role_provenance FROM v2_segments g
      WHERE g.channel_id=? AND g.role=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
      AND g.role_provenance NOT IN ('name_hint_only','database_hint_only')'''+active,(cid,role)).fetchall()
    if not segments:raise ValueError('参量没有已确认用途且未隔离的分片')
    domain={r['time_domain'] for r in segments}
    if len(domain)!=1 or next(iter(domain)) not in ('datetime_utc','datetime_naive','numeric_unspecified'):
        raise ValueError('事件时间域不明确或冲突')
    domain=next(iter(domain))
    clock=_clock_contracts(conn,[r['id'] for r in segments],domain)
    if domain=='numeric_unspecified' and (not clock or clock[0]=='legacy_unknown_reference' or clock[1] not in ('s','ms','us','ns')):
        raise ValueError('数值时间事件必须先完成时基与单位治理')
    return domain,clock,[r['id'] for r in segments]


def _event_list(conn, channel_id, role, *, include_retracted=False):
    if not _has(conn,'v2_fault_events'):return []
    sql='''SELECT id,channel_id,role,time_domain,time_reference,time_unit,start_raw,end_raw,
       label,fault_type,note,status,revision,created_utc,updated_utc FROM v2_fault_events
       WHERE channel_id=? AND role=?'''
    if not include_retracted:sql+=" AND status='active'"
    sql+=' ORDER BY id DESC LIMIT 500'
    return [dict(r) for r in conn.execute(sql,(channel_id,role))]


def _run_level_events(conn, channel_id, role):
    """Show source-scoped run truth (including test's validation view)."""
    if role not in ('test', 'simulation') or not _has(conn, 'v2_run_annotations'):
        return []
    with_location = _has(conn, 'v2_run_fault_locations')
    join_location = 'LEFT JOIN v2_run_fault_locations l ON l.source_id=a.source_id' if with_location else ''
    component = "COALESCE(l.subsystem || '/' || l.device, a.fault_component)" if with_location else 'a.fault_component'
    split = "AND a.split='validation'" if role=='test' else ''
    return [dict(row) for row in conn.execute(f"""
      SELECT -a.source_id AS id, ? AS channel_id, ? AS role,
       'numeric_unspecified' AS time_domain,
       ('elapsed_run:' || a.token) AS time_reference,'s' AS time_unit,
       CAST(a.onset_s AS TEXT) AS start_raw,
       CAST(a.fault_end_exclusive_s AS TEXT) AS end_raw,
       1 AS label, a.fault_type AS fault_type,
       ('仿真运行级故障真值，注入部件: ' || {component} ||
        '；不代表该遥测参量本身发生故障') AS note,
       'active' AS status,0 AS revision,
       'targets.zip' AS created_utc,'targets.zip' AS updated_utc,
       'simulation_run' AS annotation_scope
       FROM v2_run_annotations a JOIN v2_segments g ON g.source_id=a.source_id
       {join_location}
       WHERE g.channel_id=? AND g.role='simulation'
         AND g.kind='telemetry' AND g.usable_for_timeseries=1
         AND a.run_role='FAULT' AND a.metadata_not_model_features=1
         AND a.onset_s IS NOT NULL {split} {active_clause(conn, 'g')}
       GROUP BY a.source_id ORDER BY a.source_id
    """, (channel_id, role, channel_id))]


def list_events(db, *, channel_id, role, include_retracted=False):
    cid=_id(channel_id,'channel_id')
    if not isinstance(include_retracted,bool):raise ValueError('include_retracted 必须是布尔值')
    with open_database(db) as conn:
        channel_events=_event_list(conn,cid,role,include_retracted=include_retracted)
        run_annotations=_run_level_events(conn,cid,role)
        return {
            'channel_id':cid,
            'role':role,
            'items':channel_events,
            'run_annotations':run_annotations,
            'overlay_policy':'channel_events_only',
            'notes':[
                'items 仅包含当前参量自己的事件标注，可直接叠加到曲线。',
                'run_annotations 表示同一次仿真运行的故障真值，仅作背景提示，不代表该参量自身故障。',
            ],
        }


def create_event(db,payload, *, actor='internal'):
    if not isinstance(payload,dict):raise ValueError('请求体必须是对象')
    cid=_id(payload.get('channel_id'),'channel_id')
    role=payload.get('role')
    start=_check_text(payload.get('start'),'开始时间',100)
    end=_check_text(payload.get('end'),'结束时间',100)
    label=payload.get('label')
    if type(label) is not int or label not in (0,1):raise ValueError('事件 label 必须为整数 0 或 1')
    if role=='train' and label!=0:raise ValueError('训练用途不能标注故障事件')
    kind=_check_text(payload.get('fault_type') if label else 'normal','故障类型',120)
    note=_check_text(payload.get('note',''),'事件说明',500,required=False)
    if payload.get('confirm') is not True:raise ValueError('新建事件需要 confirm=true')
    def work(conn):
        domain,clock,segments=_get_scope(conn,cid,role)
        left=_timestamp(start,domain);right=_timestamp(end,domain)
        if left>=right:raise ValueError('事件结束时间必须晚于开始时间；区间为 [start,end)')
        # Require a real sample in the annotated interval, not merely intersecting min/max coverage.
        column={'datetime_utc':'time_utc_us','datetime_naive':'time_sort_text','numeric_unspecified':'time_sort_num'}[domain]
        slots=','.join('?'*len(segments))
        exists=conn.execute(f'''SELECT 1 FROM v2_samples WHERE segment_id IN ({slots})
                        AND {column}>=? AND {column}<? LIMIT 1''',(*segments,left,right)).fetchone()
        if not exists:raise ValueError('事件区间没有有效采样点，拒绝空标注')
        key_start='start_text' if domain=='datetime_naive' else 'start_num'
        key_end='end_text' if domain=='datetime_naive' else 'end_num'
        overlap=conn.execute(f'''SELECT id FROM v2_fault_events WHERE channel_id=? AND role=?
                      AND time_domain=? AND status='active' AND {key_start}<? AND {key_end}>? LIMIT 1''',
                      (cid,role,domain,right,left)).fetchone()
        if overlap:raise ValueError(f'事件与现有事件 #{overlap[0]} 重叠，请先核实标签')
        stamp=_now()
        cursor=conn.execute('''INSERT INTO v2_fault_events(channel_id,role,time_domain,time_reference,
           time_unit,start_raw,end_raw,start_num,end_num,start_text,end_text,label,fault_type,note,created_utc,updated_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
           (cid,role,domain,clock[0],clock[1],start,end,
            None if domain=='datetime_naive' else left,None if domain=='datetime_naive' else right,
            left if domain=='datetime_naive' else None,right if domain=='datetime_naive' else None,
            label,kind,note,stamp,stamp))
        event_id=cursor.lastrowid
        _audit(conn,'event',event_id,'create',{}, {'channel_id':cid,'role':role,'start':start,'end':end,
            'label':label,'fault_type':kind,'time_domain':domain,'clock':clock},note or '明确添加事件',actor)
        return {'status':'created','event_id':event_id,'channel_id':cid,'role':role}
    return _write(db,work)


def retract_event(db,payload, *, actor='internal'):
    if not isinstance(payload,dict) or payload.get('confirm') is not True:
        raise ValueError('撤回事件需要 confirm=true')
    eid=_id(payload.get('event_id'),'event_id')
    expected=payload.get('expected_revision')
    if type(expected) is not int or expected<0:raise ValueError('必须提交 expected_revision')
    reason=_check_text(payload.get('reason'),'撤回原因',500)
    def work(conn):
        event=conn.execute('SELECT * FROM v2_fault_events WHERE id=?',(eid,)).fetchone()
        if not event or event['status']!='active':raise ValueError('事件不存在或已撤回')
        if event['revision']!=expected:raise ValueError('事件版本已变化，请刷新后重试')
        conn.execute("UPDATE v2_fault_events SET status='retracted',revision=revision+1,updated_utc=? WHERE id=?",(_now(),eid))
        _audit(conn,'event',eid,'retract',{'status':'active','revision':expected},
               {'status':'retracted','revision':expected+1},reason,actor)
        return {'status':'retracted','event_id':eid,'revision':expected+1}
    return _write(db,work)


def list_audit(db, *, limit=100):
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('limit 必须为 1～500')
    with open_database(db) as conn:
        if not _has(conn,'v2_governance_audit'):return {'items':[]}
        return {'items':[dict(r) for r in conn.execute('''SELECT id,entity,entity_id,actor,action,old_json,new_json,reason,recorded_utc
                   FROM v2_governance_audit ORDER BY id DESC LIMIT ?''',(limit,))]}


def evaluate_v2_events(db, result, *, channel_ids, role='test'):
    """Compute objective event metrics from a V2 annotation snapshot.

    This runs AFTER the detector has made its decisions, never changes labels,
    scores, detector configuration or its alarm post-processing policy. Numeric
    source clock units are converted to seconds exclusively for time metrics.
    """
    from scripts.server.event_scoring import summarize_events

    if role != 'test':
        raise ValueError('事件级检测指标仅对测试用途提供')
    if not isinstance(channel_ids, list) or not 1 <= len(channel_ids) <= 8:
        raise ValueError('评估需要明确的逻辑参量清单')
    with open_database(db) as conn:
        channels=[]; events=[]; all_clock=[]
        for cid in channel_ids:
            cid=_id(cid,'channel_id')
            domain,clock,seg_ids=_get_scope(conn,cid,role)
            channels.append((cid,domain,clock))
            all_clock.append((domain,clock))
            for item in _event_list(conn,cid,role):
                if (item['time_domain'],(item['time_reference'],item['time_unit'])) != (domain,clock):
                    raise ValueError('事件标注的时基与当前数据不同，请先治理后再评估')
                events.append(item)
        if len(set(map(str,all_clock))) != 1:
            raise ValueError('所选参量的时基不一致，无法计算联合事件指标')
    if len(events)>500:
        return {'event_metrics':{'status':'unavailable','reason':'有效事件超过500条，需缩小评估范围或分页审查'},
                'event_overlay':{'status':'unavailable','items':[]}}
    domain,clock=all_clock[0]
    source_names=[f'v2:channel:{cid}' for cid in channel_ids]
    factor=1.0
    if domain=='numeric_unspecified':
        factor={'s':1.0,'ms':1000.0,'us':1_000_000.0,'ns':1_000_000_000.0}[clock[1]]
    evaluated=[]; overlay=[]
    for item in events:
        public={'id':item['id'],'channel_id':item['channel_id'],'role':role,
            'start_timestamp':item['start_raw'],'end_timestamp':item['end_raw'],
            'timestamp_kind':{'datetime_utc':'utc','datetime_naive':'local','numeric_unspecified':'numeric'}[domain],
            'label':item['label'],'fault_type':item['fault_type'],
            'source_table':f'v2:channel:{item["channel_id"]}',
            'time_reference':item['time_reference'],'time_unit':item['time_unit']}
        overlay.append(public)
        scored=dict(public)
        if factor!=1.0:
            scored['start_timestamp']=str(float(item['start_raw'])/factor)
            scored['end_timestamp']=str(float(item['end_raw'])/factor)
        evaluated.append(scored)
    score_result=result
    if factor!=1.0 and isinstance(result.get('rows'),list):
        score_result={**result, 'rows':[
            {**r,'timestamp':str(float(r['timestamp'])/factor)} for r in result['rows']
        ]}
    # The experiment may cover only a small subset of an entire test dataset.
    # Keep only intervals actually intersecting the observed test window.
    # Source annotations outside the run must not inflate missed/unknown counts.
    from scripts.core.multi_table_fusion import _moment, _as_seconds
    times=[_as_seconds(_moment(row['timestamp'])) for row in score_result.get('rows',[])]
    if times:
        left,right=min(times),max(times)
        filtered=[(public,scored) for public,scored in zip(overlay,evaluated)
                  if _as_seconds(_moment(scored['end_timestamp']))>left
                  and _as_seconds(_moment(scored['start_timestamp']))<=right]
        overlay=[public for public,_ in filtered]
        evaluated=[scored for _,scored in filtered]
    metrics=summarize_events(score_result,evaluated,sources=source_names) if evaluated else {
        'status':'no_annotations','reason':'评估时间窗口内没有有效 V2 事件标注，未计算事件级指标'}
    return {'event_metrics':metrics,
            'event_overlay':{'status':'ok' if overlay else 'none','sources':source_names,
                'interval_semantics':'[start,end)','total_intervals':len(overlay),'items':overlay}}


def dataset_summary(db):
    """Read-only V2 grouped inventory; old unmapped records remain separate."""
    with open_database(db) as conn:
        sql = '''SELECT c.namespace,g.role,COUNT(*) AS segment_count,
                  COUNT(DISTINCT c.id) AS channel_count,
                  SUM(g.source_row_count) AS reported_rows,
                  SUM(CASE WHEN g.role_provenance IN ('name_hint_only','database_hint_only','none')
                      THEN 1 ELSE 0 END) AS unverified_segments
                  FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                  WHERE g.kind='telemetry' AND g.usable_for_timeseries=1'''
        sql += active_clause(conn,'g')
        sql += ' GROUP BY c.namespace,g.role ORDER BY c.namespace,g.role'
        items=[dict(row) for row in conn.execute(sql)]
        total=conn.execute("SELECT COUNT(*) FROM v2_segments WHERE kind='telemetry' AND usable_for_timeseries=1").fetchone()[0]
        inactive=(conn.execute('SELECT COUNT(*) FROM v2_segment_governance WHERE active=0').fetchone()[0]
                  if _has(conn,'v2_segment_governance') else 0)
        legacy=conn.execute('SELECT COUNT(*) FROM v2_legacy_rows').fetchone()[0]
        issues=conn.execute('SELECT COUNT(*) FROM v2_issues').fetchone()[0]
        return {'items':items,'dataset_count':len(items),
                'telemetry_segments_total':total,'quarantined_segments':inactive,
                'legacy_rows_preserved':legacy,'unresolved_issue_records':issues,
                'row_count_semantics':'segment_source_row_count_not_distinct_samples'}
