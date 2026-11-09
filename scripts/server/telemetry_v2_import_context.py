"""Additive source-level identity, evidence scope and import quality for V2.

This metadata is not a detector feature, a sample label rewrite or a replacement
for the canonical targets archive. It is committed in the SAME SQLite transaction
as samples. Older V2 databases and existing import callers remain valid.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import statistics
from datetime import datetime, timezone

TOKEN = re.compile(r'[0-9a-f]{24}\Z')
SPLITS = frozenset({'train','validation','test_confirmation','ood_scene','ood_joint','ood_severity'})
SCOPE = frozenset({'run','component','parameter','unknown'})
ORIGINS = frozenset({'simulation','flight','ground_test','other'})

DDL = '''CREATE TABLE IF NOT EXISTS v2_import_context (
    source_id INTEGER PRIMARY KEY REFERENCES v2_sources(id),
    run_token TEXT, dataset_split TEXT, group_id TEXT,
    run_role TEXT, origin_type TEXT NOT NULL, label_scope TEXT NOT NULL,
    evidence_origin TEXT NOT NULL, evidence_sha256 TEXT,
    fault_type TEXT, fault_component TEXT, fault_onset_s REAL,
    metadata_json TEXT NOT NULL, quality_json TEXT NOT NULL,
    recorded_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_import_context_token ON v2_import_context(run_token);
CREATE INDEX IF NOT EXISTS idx_v2_import_context_split ON v2_import_context(dataset_split,group_id);'''


def _optional(value, name, max_len=120):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > max_len or value.strip() != value or any(ord(ch)<32 for ch in value):
        raise ValueError(name+' 格式无效')
    return value


def _target(obj):
    """Validate user-supplied *single member* metadata; no trust promotion."""
    from scripts.target_archive_evidence import REQUIRED
    if not isinstance(obj, dict) or set(obj) != REQUIRED:
        raise ValueError('标签 JSON 必须具有 targets 单个成员的全部字段')
    token=obj.get('token'); group=obj.get('group'); split=obj.get('split'); role=obj.get('role')
    if not isinstance(token,str) or not TOKEN.fullmatch(token) or not isinstance(group,str) or not TOKEN.fullmatch(group):
        raise ValueError('标签 token/group 格式无效')
    if split not in SPLITS or role not in ('NORMAL','FAULT') or obj.get('metadata_not_model_features') is not True:
        raise ValueError('标签 split/role 或禁止模型特征声明无效')
    if role=='NORMAL':
        if any(obj.get(key) is not None for key in ('fault','component','onset_s')):
            raise ValueError('正常运行不得携带故障字段')
    else:
        if any(not isinstance(obj.get(key),str) or not obj[key] or len(obj[key])>120 for key in ('fault','component')):
            raise ValueError('故障运行缺少有效故障源及类型')
        onset=obj.get('onset_s')
        if type(onset) not in (int,float) or not math.isfinite(onset) or onset<0:
            raise ValueError('故障起点无效')
    return obj


def parse_context(payload, *, filename, labels, role):
    raw=payload.get('import_context')
    if raw is None:
        return None
    if not isinstance(raw,dict):
        raise ValueError('import_context 必须是对象')
    allowed={'run_token','dataset_split','group_id','run_role','origin_type','label_scope','target_metadata'}
    if set(raw)-allowed:
        raise ValueError('运行元数据包含未声明字段')
    context={key:_optional(raw.get(key),key) for key in ('run_token','dataset_split','group_id','run_role')}
    context['origin_type']=raw.get('origin_type') or 'other'
    context['label_scope']=raw.get('label_scope') or 'unknown'
    if context['origin_type'] not in ORIGINS or context['label_scope'] not in SCOPE:
        raise ValueError('来源类型或标签作用范围无效')
    if context['run_token'] and not TOKEN.fullmatch(context['run_token']):
        raise ValueError('运行 token 必须是24位十六进制标识')
    if context['group_id'] and not TOKEN.fullmatch(context['group_id']):
        raise ValueError('group 必须是24位十六进制标识')
    if context['dataset_split'] and context['dataset_split'] not in SPLITS:
        raise ValueError('split 不在正式划分列表内')
    if context['run_role'] and context['run_role'] not in ('NORMAL','FAULT'):
        raise ValueError('运行标签只能是 NORMAL/FAULT')
    evidence=raw.get('target_metadata')
    context.update(evidence_origin='user_declaration',evidence_sha256=None,
                   fault_type=None,fault_component=None,fault_onset_s=None)
    if evidence is not None:
        target=_target(evidence)
        basename=re.match(r'^([0-9a-f]{24})\.csv(?:\(\d+\))?(?:\.gz)?$',filename,re.I)
        if basename and basename.group(1).lower()!=target['token']:
            raise ValueError('文件名中的运行 token 与附带的 targets JSON 不一致')
        for field,target_field in [('run_token','token'),('dataset_split','split'),('group_id','group'),('run_role','role')]:
            if context[field] is not None and context[field] != target[target_field]:
                raise ValueError('标签 JSON 与已填写 '+field+' 冲突')
            context[field]=target[target_field]
        if context['label_scope'] not in ('unknown','run'):
            raise ValueError('targets 标签仅证明运行级真值，不得声称是部件/参量级标签')
        context['label_scope']='run'
        context['evidence_origin']='user_supplied_target_member_unverified'
        # Canonical raw-member SHA cannot be reconstructed from reserialized JSON.
        # This is only a reproducible normalized payload digest, NOT archive verification.
        context['evidence_sha256']=hashlib.sha256(json.dumps(target,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        context.update(fault_type=target['fault'],fault_component=target['component'],fault_onset_s=target['onset_s'])
    if (context['dataset_split'] or context['group_id'] or context['run_role']) and not context['run_token']:
        raise ValueError('split/group/运行标签要求明确运行 token')
    if context['run_role']=='FAULT' and role=='train':
        raise ValueError('故障运行不能作为 AAS 正常训练用途导入')
    if context['dataset_split'] in SPLITS-{'train'} and role=='train':
        raise ValueError('保留评估划分不得作为正常训练用途导入')
    if context['run_role']=='NORMAL' and 1 in labels:
        raise ValueError('运行声明 NORMAL 与文件中的故障标签冲突')
    if context['label_scope']=='component' or context['label_scope']=='parameter':
        # No explicit range evidence is supplied by this import contract.
        raise ValueError('部件/参量级真值必须走独立的复核事件导入，不能在普通文件中声明')
    # Keep a clear limitation for run-level labels: source file labels are not
    # upgraded to per-channel ground truth under any circumstances.
    context['metadata_json']=json.dumps({k:v for k,v in context.items() if k not in ('metadata_json',)},ensure_ascii=False,sort_keys=True)
    return context


def ensure_context_table(conn):
    for statement in DDL.split(';'):
        if statement.strip():conn.execute(statement)


def check_conflicts(conn, context, *, namespace=None, model=None):
    if not context:return
    ensure_context_table(conn)
    token=context['run_token'];group=context['group_id'];split=context['dataset_split']
    if token:
        for old in conn.execute('''SELECT dataset_split,group_id,run_role,label_scope FROM v2_import_context
                                  WHERE run_token=?''',(token,)):
            if any(a is not None and b is not None and a!=b for a,b in zip(old,(split,group,context['run_role'],context['label_scope']))):
                raise ValueError('同一运行 token 的历史 split/group/role/标签范围冲突')
    if token and namespace and model:
        for old in conn.execute('''SELECT DISTINCT c.namespace,c.model FROM v2_import_context x
              JOIN v2_segments g ON g.source_id=x.source_id
              JOIN v2_channels c ON c.id=g.channel_id WHERE x.run_token=?''',(token,)):
            if (old[0],old[1]) != (namespace,model):
                raise ValueError('同一运行 token 的型号或命名空间不一致')
    if group and split:
        if conn.execute('''SELECT 1 FROM v2_import_context WHERE group_id=? AND dataset_split IS NOT NULL
                           AND dataset_split<>? LIMIT 1''',(group,split)).fetchone():
            raise ValueError('同一 group 横跨 split，存在数据泄漏风险')
    # The canonical simulation annotations and newly imported sources must not
    # silently share a token or contradict the same group's official split.
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'v2_run_annotations' in tables:
        if token and conn.execute('SELECT 1 FROM v2_run_annotations WHERE token=? LIMIT 1',(token,)).fetchone():
            raise ValueError('该 token 已有正式运行标注；请核对是否重复导入相同运行')
        if group and split and conn.execute('''SELECT 1 FROM v2_run_annotations WHERE group_id=?
                 AND split<>? LIMIT 1''',(group,split)).fetchone():
            raise ValueError('导入 group 与已标注运行的 split 冲突，可能产生泄漏')


def record_context(conn, source_id, context, quality):
    if context is None:return
    ensure_context_table(conn)
    conn.execute('''INSERT INTO v2_import_context(source_id,run_token,dataset_split,group_id,
       run_role,origin_type,label_scope,evidence_origin,evidence_sha256,
       fault_type,fault_component,fault_onset_s,metadata_json,quality_json,recorded_utc)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
       source_id,context['run_token'],context['dataset_split'],context['group_id'],context['run_role'],
       context['origin_type'],context['label_scope'],context['evidence_origin'],context['evidence_sha256'],
       context['fault_type'],context['fault_component'],context['fault_onset_s'],context['metadata_json'],
       json.dumps(quality,ensure_ascii=False,sort_keys=True),datetime.now(timezone.utc).isoformat()))


def quality_report(data, *, time_domain, time_unit):
    # Data rows are already sorted; source_ordinal preserves original order.
    first=next(iter(data.values()))
    rows=list(first)
    indexed=[(r[4] if time_domain=='datetime_utc' else r[3]) for r in rows]
    delta=[float(b)-float(a) for a,b in zip(indexed,indexed[1:])]
    # UTC keys are in microseconds, numeric timestamps use declared unit.
    scale=1e6 if time_domain=='datetime_utc' else {'s':1,'ms':1e3,'us':1e6,'ns':1e9}[time_unit]
    steps=[d/scale for d in delta if d>0]
    median=statistics.median(steps) if steps else None
    gaps=sum(step>3*median for step in steps) if median and median>0 else 0
    order_reversals=sum(a[8]>b[8] for a,b in zip(rows,rows[1:]))
    count={('unknown' if value is None else str(value)):sum(r[6]==value for r in rows) for value in (0,1,None)}
    missing={name:sum(r[5] is None for r in values) for name,values in data.items()}
    return {'total_rows':len(rows),'label_counts':count,'missing_by_column':missing,
            'sample_period_median_seconds':median,'gaps_gt_3x_median':gaps,
            'source_order_reversals':order_reversals,
            'quality_scope':'import_data_only_no_fault_inference'}
