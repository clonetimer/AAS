"""Source-specific reviewed observation events (never inferred from fault runs).

An event means a reviewer explicitly annotated THIS telemetry parameter in THIS
run. It does not assert a new physical component failure, nor alter raw labels.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
import sqlite3

TABLE_SQL = '''CREATE TABLE IF NOT EXISTS v2_source_parameter_events(
 id INTEGER PRIMARY KEY,
 source_id INTEGER NOT NULL REFERENCES v2_sources(id),
 segment_id INTEGER NOT NULL REFERENCES v2_segments(id),
 start_s REAL NOT NULL,
 end_s REAL NOT NULL,
 label INTEGER NOT NULL CHECK(label IN (0,1)),
 fault_type TEXT NOT NULL DEFAULT '',
 reviewer TEXT NOT NULL,
 evidence_reference TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_utc TEXT NOT NULL,
 CHECK(end_s>start_s)
);
CREATE INDEX IF NOT EXISTS idx_v2_source_parameter_events
 ON v2_source_parameter_events(source_id,segment_id,start_s,end_s);'''


def _text(v, name, maxlen=300, required=True):
    if not isinstance(v,str) or len(v)>maxlen or (required and not v.strip()) or any(ord(c)<32 for c in v):
        raise ValueError(f'{name} 必须是经过确认的文本，不得为空或包含控制字符')
    return v.strip()


def read_events(conn, source_id, segment_id, clock):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_source_parameter_events'").fetchone():
        return None
    rows=conn.execute('''SELECT start_s,end_s,label,id,evidence_reference FROM v2_source_parameter_events
               WHERE source_id=? AND segment_id=? ORDER BY start_s,id''', (source_id,segment_id)).fetchall()
    if not rows:return None
    if clock is None or (clock['unit']!='s' or not str(clock['reference']).startswith('elapsed_run:')):
        raise ValueError('来源级参量事件仅支持已确认的秒级仿真时钟')
    intervals=[];end=-math.inf
    for row in rows:
        a,b=float(row['start_s']),float(row['end_s'])
        if not (math.isfinite(a) and math.isfinite(b) and 0<=a<b and a>=end):
            raise ValueError('来源级参量事件重叠或时间无效')
        intervals.append((a,b,int(row['label'])));end=b
    return intervals, {'source':'v2_source_parameter_events','scope':'parameter',
        'event_count':len(intervals), 'run_source_id':source_id,
        'evidence_references':[r['evidence_reference'] for r in rows]}


def install_reviewed_events(conn, entries):
    """Call only within a transaction on a user-requested NEW database copy.

    Expected entry fields: token, feature_key, start_s, end_s, label,
    reviewer, evidence_reference. Extra note and fault_type are optional.
    """
    if not isinstance(entries,list) or not entries or len(entries)>5000:
        raise ValueError('必须提交 1～5000 条明确审查的参量事件')
    conn.executescript(TABLE_SQL)
    results=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)-{'token','feature_key','start_s','end_s','label','reviewer','evidence_reference','note','fault_type'}:
            raise ValueError('参量事件字段不合法')
        token=_text(entry.get('token'),'运行 token',120)
        feature=_text(entry.get('feature_key'),'精确参量字段',160)
        reviewer=_text(entry.get('reviewer'),'复核人员',120)
        reference=_text(entry.get('evidence_reference'),'证据引用',300)
        note=_text(entry.get('note',''),'说明',500,required=False)
        label=entry.get('label')
        if type(label) is not int or label not in (0,1):raise ValueError('参量事件标签只能是整数 0/1')
        fault_type=_text(entry.get('fault_type','') if label else '', '故障类型',120,required=bool(label))
        try:a,b=float(entry['start_s']),float(entry['end_s'])
        except (TypeError,ValueError,KeyError) as exc:raise ValueError('时间区间必须给出有效秒数') from exc
        if not (math.isfinite(a) and math.isfinite(b) and 0<=a<b):raise ValueError('事件时间区间必须为有限非负 [start,end)')
        target=conn.execute('''SELECT a.source_id,g.id AS segment_id,k.reference,k.unit,
                     g.time_domain,g.role FROM v2_run_annotations a JOIN v2_segments g ON g.source_id=a.source_id
                     JOIN v2_segment_clock k ON k.segment_id=g.id
                     WHERE a.token=? AND g.source_table=? AND g.kind='telemetry'
                       AND g.usable_for_timeseries=1''',(token,feature)).fetchall()
        if len(target)!=1:raise ValueError('证据对应的运行+参量不是唯一有效来源: '+token+' / '+feature)
        row=target[0]
        if row['time_domain']!='numeric_unspecified' or row['unit']!='s' or row['role']!='simulation' or not str(row['reference']).startswith('elapsed_run:'):
            raise ValueError('事件时间基准不符合独立仿真运行秒时钟')
        segment=row['segment_id'];sid=row['source_id']
        if not conn.execute('''SELECT 1 FROM v2_samples WHERE segment_id=? AND time_sort_num>=?
                             AND time_sort_num<? LIMIT 1''',(segment,a,b)).fetchone():
            raise ValueError('事件区间没有对应遥测采样')
        if conn.execute('''SELECT 1 FROM v2_source_parameter_events WHERE source_id=? AND segment_id=?
                  AND start_s<? AND end_s>? LIMIT 1''',(sid,segment,b,a)).fetchone():
            raise ValueError('参量事件区间重叠，拒绝合并或覆盖')
        cur=conn.execute('''INSERT INTO v2_source_parameter_events
            (source_id,segment_id,start_s,end_s,label,fault_type,reviewer,evidence_reference,note,created_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',(sid,segment,a,b,label,fault_type,reviewer,reference,note,
                          datetime.now(timezone.utc).isoformat(timespec='seconds')))
        results.append({'id':cur.lastrowid,'token':token,'feature_key':feature,'label':label})
    return results
