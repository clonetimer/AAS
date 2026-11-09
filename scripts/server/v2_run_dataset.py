"""Run-isolated subset selection for verified V2 telemetry targets.

A feature is a *semantic telemetry column*, not a physical SQLite table or a
run-specific channel ID. Time is relative seconds **within each run only**.
Original target splits and groups are authoritative. Training fits only on NORMAL
runs in the train split; held-out runs retain their validation, confirmation or
OOD split identity. Never derive detector features from fault metadata.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import json
import math
from pathlib import Path

from scripts.server.feature_schema import (assert_feature_schema_matches,
                                           attest_feature_schema, canonical_feature_keys)
from scripts.server.telemetry_v2_access import open_database, relative_seconds
from scripts.server.telemetry_v2_training import MAX_ALIGNED, MAX_FEATURES, ALLOWED_METHODS
from scripts.target_archive_evidence import HELDOUT_SPLITS


def _finite(value, name):
    if value in (None, ''): return None
    if isinstance(value, bool): raise ValueError(f'{name} 格式错误')
    number=relative_seconds(value)
    if number < 0: raise ValueError(f'{name} 不能早于 2026-01-01 00:00:00（显示基准）')
    return number


def _parse_selection(selection, *, role):
    if not isinstance(selection, dict) or role not in ('train','test'):
        raise ValueError('必须提供正确的运行级训练或测试选择')
    allowed = {'source_ids','feature_keys','start','end','method','tolerance_seconds',
               'coordinate_mode','frame_sequence_id'}
    if set(selection)-allowed:
        raise ValueError('运行级选择出现未支持字段: '+','.join(sorted(set(selection)-allowed)))
    ids = selection.get('source_ids')
    keys = selection.get('feature_keys')
    if not isinstance(ids, list) or not ids or len(ids)>64 or any(type(x) is not int or x<=0 for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('请选择不重复的仿真运行 source_ids')
    if not isinstance(keys,list) or not 1<=len(keys)<=MAX_FEATURES or any(not isinstance(x,str) or not x.strip() or len(x)>160 for x in keys) or len(set(keys))!=len(keys):
        raise ValueError(f'请选择 1～{MAX_FEATURES} 个不重复的参量字段')
    # Training fixes the canonical column order so re-training the same parameter
    # set can never produce a transposed-but-shape-identical model. Evaluation
    # keeps the published model's stored order; sorting here would reject every
    # model trained before the order was canonicalised.
    if role=='train':
        keys=canonical_feature_keys(keys)
    frame_mode=selection.get('coordinate_mode','time')=='frame'
    if selection.get('coordinate_mode','time') not in ('time','frame'):
        raise ValueError('坐标模式必须为 time 或 frame')
    if frame_mode:
        if not isinstance(selection.get('frame_sequence_id'),str) or not selection['frame_sequence_id'].strip():
            raise ValueError('帧模式必须指定帧序列 ID')
        start=selection.get('start');end=selection.get('end')
        if any(x is not None and (type(x) is not int or x<1) for x in (start,end)):
            raise ValueError('帧范围必须是从 1 开始的整数')
    else:
        start,end=_finite(selection.get('start'),'开始时间'),_finite(selection.get('end'),'结束时间')
    if start is not None and end is not None and start>end:
        raise ValueError('时间范围起点晚于终点')
    method = selection.get('method','exact')
    tol = _finite(selection.get('tolerance_seconds',0),'时间容差')
    if method not in ALLOWED_METHODS or tol is None or tol>3600 or (method=='exact' and tol!=0):
        raise ValueError('时间对齐仅支持 exact/nearest/previous；exact 容差必须为 0')
    if frame_mode and (method!='exact' or tol!=0):
        raise ValueError('帧模式只支持按帧号精确对齐')
    return ids, keys, start, end, method, tol


def _uploaded_runs(conn, *, include_unlabeled=False):
    """Qualified user-imported V2 CSV sources, never inferred from physical tables.

    Both train and test require user-explicit role and sample labels. Stable
    groups derive from namespace + model + clock reference, so reimported slices
    of the same clock cannot cross the train/test boundary.
    """
    frame_cols={row[1] for row in conn.execute('PRAGMA table_info(v2_segments)')}
    frame_enabled='coordinate_mode' in frame_cols
    mode_select=("MIN(g.coordinate_mode) AS coordinate_mode,COUNT(DISTINCT g.coordinate_mode) AS modes,"
                 "MIN(g.frame_sequence_id) AS frame_sequence_id,COUNT(DISTINCT g.frame_sequence_id) AS sequences,"
                 if frame_enabled else "'time' AS coordinate_mode,1 AS modes,NULL AS frame_sequence_id,0 AS sequences,")
    usable=("(g.usable_for_timeseries=1 OR g.coordinate_mode='frame')" if frame_enabled else 'g.usable_for_timeseries=1')
    q = conn.execute(f'''SELECT g.source_id,{mode_select}MIN(c.model) AS model_alias,
            MIN(c.namespace) AS namespace,MIN(g.role) AS data_role,
            COUNT(DISTINCT g.role) AS roles,MIN(g.time_domain) AS clock_domain,
            COUNT(DISTINCT g.time_domain) AS clock_domains,
            MIN(k.reference) AS reference,COUNT(DISTINCT k.reference) AS refs,
            MIN(k.unit) AS unit,COUNT(DISTINCT k.unit) AS units,
            SUM(g.source_row_count) AS sampling_rows
        FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
        LEFT JOIN v2_segment_clock k ON k.segment_id=g.id
        WHERE g.role_provenance IN ('user_explicit','v2_preprocess_derived') AND g.role IN ('train','test')
            AND g.kind='telemetry' AND {usable}
        GROUP BY g.source_id ORDER BY g.source_id''')
    rows=[]
    has_context=bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_import_context'").fetchone())
    for r in q:
        if r['roles'] != 1 or r['clock_domains'] != 1 or r['modes'] != 1:
            continue
        # Run-level matrices currently require numeric seconds. UTC time-series
        # can be browsed; enabling them for training needs an explicit epoch view.
        frame_mode=r['coordinate_mode']=='frame'
        if frame_mode and (r['sequences']!=1 or not r['frame_sequence_id']):
            continue
        if not frame_mode and (r['refs'] != 1 or r['units'] != 1 or
                               r['clock_domain'] != 'numeric_unspecified' or r['unit'] != 's'):
            continue
        source_id = r['source_id']; role=r['data_role']
        label_counts=conn.execute('''SELECT s.label_int,COUNT(*) FROM v2_samples s
            JOIN v2_segments g ON g.id=s.segment_id
            WHERE g.source_id=? GROUP BY s.label_int''',(source_id,)).fetchall()
        counts={k:v for k,v in label_counts}
        unknown_only = (role == 'test' and set(counts) == {None} and counts[None] > 0)
        if ((any(key not in (0,1) for key in counts) and not (include_unlabeled and unknown_only))
                or (role == 'train' and counts.get(1,0))):
            continue
        r=dict(r)
        group=('import:frame:'+str(source_id)+':'+r['frame_sequence_id'] if frame_mode else
               'import:'+r['namespace']+':'+r['model_alias']+':'+r['reference'])
        context=(conn.execute('''SELECT run_token,dataset_split,group_id,run_role,label_scope,evidence_origin,
                      evidence_sha256,fault_onset_s FROM v2_import_context WHERE source_id=?''' ,(source_id,)).fetchone()
                 if has_context else None)
        if context and context['run_token']:
            if context['dataset_split'] in HELDOUT_SPLITS and role!='test':continue
            if context['dataset_split']=='train' and role!='train':continue
            if context['run_role']=='FAULT' and role=='train':continue
            group=context['group_id'] or 'import:'+context['run_token']
        # Derived sources inherit the original physical run's group. A derived
        # run must not bypass original train/validation separation or look like
        # a fresh independent normal run when training/testing.
        provenance=conn.execute('SELECT role_provenance,id FROM v2_segments WHERE source_id=? LIMIT 1',
                                (source_id,)).fetchone()
        if provenance['role_provenance']=='v2_preprocess_derived':
            has_q=conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_preprocess_quality'").fetchone()
            if not has_q: continue
            origin=conn.execute('''SELECT DISTINCT g.source_id,g.role,c.namespace,c.model,
                                    k.reference,k.unit
                FROM v2_preprocess_quality q JOIN v2_segments g ON g.id=q.input_segment_id
                JOIN v2_channels c ON c.id=g.channel_id
                LEFT JOIN v2_segment_clock k ON k.segment_id=g.id
                WHERE q.output_segment_id=?''',(provenance['id'],)).fetchall()
            if len(origin)!=1:continue  # never unify unrelated runs into one new run
            o=origin[0]
            if o['role']=='simulation':
                annotation=conn.execute('SELECT group_id,split,run_role FROM v2_run_annotations WHERE source_id=?',
                                        (o['source_id'],)).fetchone()
                if not annotation:continue
                permitted=(role=='train' and annotation['split']=='train' and annotation['run_role']=='NORMAL') or (role=='test' and annotation['split'] in HELDOUT_SPLITS)
                if not permitted:continue
                group=annotation['group_id']
            elif o['role']==role and o['reference'] and o['unit']=='s':
                group='import:'+o['namespace']+':'+o['model']+':'+o['reference']
            else:
                continue
        actual_split=(context['dataset_split'] if context and context['dataset_split'] else role)
        actual_role=('UNLABELED' if unknown_only else
                     context['run_role'] if context and context['run_role'] else
                     'NORMAL' if role=='train' else 'LABELED')
        if role=='train' and (actual_split!='train' or actual_role!='NORMAL'):continue
        if role=='test' and actual_split not in (*HELDOUT_SPLITS,'test'):continue
        coordinate_field='s.frame_index' if frame_mode else 's.time_sort_num'
        unknown_frames = (conn.execute(f'''SELECT COUNT(DISTINCT {coordinate_field}) FROM v2_samples s
            JOIN v2_segments g ON g.id=s.segment_id
            WHERE g.source_id=? AND s.label_int IS NULL AND {coordinate_field} IS NOT NULL''',
            (source_id,)).fetchone()[0] if unknown_only else 0)
        rows.append({'source_id':source_id,'model_alias':r['model_alias']+' · '+(r['frame_sequence_id'] if frame_mode else r['reference']),
            'split':actual_split,'group_id':group,
            'run_role':actual_role,
            'normal_frames':counts.get(0,0),'fault_frames':counts.get(1,0),
            'unknown_frames':unknown_frames,
            'onset_s':context['fault_onset_s'] if context else None,
            'allowed_role':'score_only' if unknown_only else role,'data_role':role,
            'coordinate_mode':r['coordinate_mode'],'frame_sequence_id':r['frame_sequence_id'],
            'target_sha256':None,'annotation_scope':'external_import',
            'label_scope':context['label_scope'] if context else 'legacy_unspecified',
            'evidence_origin':context['evidence_origin'] if context else 'legacy_import',
            'metadata_not_model_features':1})
    return rows


def catalog(db_path:Path):
    """Unified run/feature/time catalog: labeled simulations and explicit imports."""
    with open_database(db_path) as conn:
        annotated=bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_annotations'").fetchone())
        runs=[]
        if annotated:
            runs=[dict(r) for r in conn.execute('''SELECT source_id, model_alias, split,group_id,run_role,
                onset_s,normal_frames,fault_frames FROM v2_run_annotations ORDER BY source_id''')]
            for r in runs:
                r['allowed_role']=('train' if r['split']=='train' and r['run_role']=='NORMAL'
                    else 'test' if r['split'] in HELDOUT_SPLITS else 'excluded')
                r['data_role']='simulation'
        uploaded=_uploaded_runs(conn, include_unlabeled=True)
        has_frame='coordinate_mode' in {row[1] for row in conn.execute('PRAGMA table_info(v2_segments)')}
        usable=("(g.usable_for_timeseries=1 OR g.coordinate_mode='frame')" if has_frame else 'g.usable_for_timeseries=1')
        runs.extend(uploaded)
        feature_rows=[]
        if annotated:
            feature_rows.extend(conn.execute('''SELECT g.source_table AS feature_key,
                c.subsystem,c.device,c.variable,c.unit,COUNT(DISTINCT g.source_id) AS run_count
                FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                JOIN v2_run_annotations a ON a.source_id=g.source_id
                WHERE g.kind='telemetry' AND g.usable_for_timeseries=1 AND g.role='simulation'
                GROUP BY g.source_table,c.subsystem,c.device,c.variable,c.unit''').fetchall())
        feature_rows.extend(conn.execute(f'''SELECT c.subsystem || '.' || c.device || '.' || c.variable AS feature_key,
                c.subsystem,c.device,c.variable,c.unit,COUNT(DISTINCT g.source_id) AS run_count
                FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                WHERE g.kind='telemetry' AND {usable}
                AND g.role_provenance IN ('user_explicit','v2_preprocess_derived') AND g.role IN ('train','test')
                GROUP BY g.source_table,c.subsystem,c.device,c.variable,c.unit''').fetchall())
        # The global picker is a discovery index, not the authoritative unit
        # for a selected run. Identical feature keys can have different units
        # in separately imported CSV files; model filtering must use the
        # physical semantics for the actual source selected by the operator.
        run_features={}
        for item in conn.execute(f'''SELECT DISTINCT g.source_id,
                c.subsystem || '.' || c.device || '.' || c.variable AS feature_key,
                c.subsystem,c.device,c.variable,c.unit
            FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
            WHERE g.kind='telemetry' AND {usable}
              AND g.role_provenance IN ('user_explicit','v2_preprocess_derived')
              AND g.role IN ('train','test')
            ORDER BY g.source_id,feature_key'''):
            record=dict(item)
            run_features.setdefault(str(record.pop('source_id')),[]).append(record)
        unique={}
        for r in feature_rows:
            obj=dict(r);key=obj['feature_key']
            if key not in unique:unique[key]=obj
            elif any(obj[k]!=unique[key][k] for k in ('subsystem','device','variable','unit')):
                # Identical external column names with different semantics must
                # be disambiguated by upstream import mapping, not guessed.
                continue
        return {'runs':runs,'features':sorted(unique.values(),key=lambda f:(f['subsystem'],f['device'],f['variable'])),
            'run_features':run_features,
            'default_train_ids':[r['source_id'] for r in runs if r['allowed_role']=='train'][:2],
            'default_test_ids':([r['source_id'] for r in runs if r['allowed_role']=='test' and r['split']=='validation'] or
                                [r['source_id'] for r in runs if r['allowed_role']=='test'])[:2],
            'max_features':MAX_FEATURES,
            'policy':'annotated simulations: train+NORMAL fit / held-out validation, confirmation or individually identified OOD; imports: explicit train/test; no metadata features',
            'heldout_splits':sorted(HELDOUT_SPLITS)}



def selection_coverage(db_path: Path, *, role: str, source_ids: list[int],
                       feature_keys: list[str], feature_semantics=None,
                       coordinate_mode='time',frame_sequence_id=None) -> dict:
    if coordinate_mode=='frame':
        from scripts.server.v2_frame_dataset import load_frame_selection
        rows,contract=load_frame_selection(db_path,selection={'source_ids':source_ids,
            'feature_keys':feature_keys,'coordinate_mode':'frame',
            'frame_sequence_id':frame_sequence_id},role='test' if role=='score_only' else role,
            score_only=role=='score_only')
        frames=[row['frame_index'] for row in rows]
        return {'role':role,'source_ids':source_ids,'feature_keys':contract['feature_keys'],
            'start_frame':min(frames),'end_frame':max(frames),'has_common_window':True,
            'time_axis':'frame_index_not_time','coordinate_mode':'frame'}
    # Intersection across every run and every feature; not a guarantee of aligned timestamps.
    score_only=role=='score_only'
    ids, features, _, _, _, _ = _parse_selection(
        {'source_ids': source_ids, 'feature_keys': feature_keys,
         'start': None, 'end': None, 'method': 'exact', 'tolerance_seconds': 0},
        role='test' if score_only else role)
    if score_only and len(ids)!=1:
        raise ValueError('无标签仅推理只能选择一个明确的测试来源')
    if score_only and feature_semantics is not None:
        if (not isinstance(feature_semantics,list) or len(feature_semantics)!=len(features)
                or any(not isinstance(item,list) or len(item)!=4 or
                       any(not isinstance(value,str) or not value or len(value)>160 for value in item)
                       for item in feature_semantics)):
            raise ValueError('所选模型的参量物理语义无效')
    with open_database(db_path) as conn:
        if score_only:
            external={item['source_id']:item for item in _uploaded_runs(conn,include_unlabeled=True)
                      if item['allowed_role']=='score_only'}
            if any(sid not in external for sid in ids):
                raise ValueError('仅推理时间范围只接受完全无标签的测试 CSV')
            runs=[external[sid] for sid in ids]
        else:
            runs = _selected(conn, ids, role)
        entries = []
        for run in runs:
            for index,key in enumerate(features):
                query='''SELECT g.id,g.time_domain
                    FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                    WHERE g.source_id=? AND g.kind='telemetry'
                      AND g.usable_for_timeseries=1 AND g.role=?'''
                params=[run['source_id'],run['data_role']]
                if score_only and feature_semantics is not None:
                    query+=' AND c.subsystem=? AND c.device=? AND c.variable=? AND c.unit=?'
                    params.extend(feature_semantics[index])
                else:
                    query+=''' AND ((?='simulation' AND g.source_table=?) OR
                        (?!='simulation' AND (c.subsystem || '.' || c.device || '.' || c.variable)=?))'''
                    params.extend([run['data_role'],key,run['data_role'],key])
                segs=conn.execute(query,params).fetchall()
                if len(segs) != 1 or segs[0]['time_domain'] != 'numeric_unspecified':
                    raise ValueError(f'{run["model_alias"]} 缺少可用参量 {key}，请调整所选参量或卫星型号')
                bounds = conn.execute('''SELECT MIN(time_sort_num) AS start_s,
                        MAX(time_sort_num) AS end_s FROM v2_samples
                        WHERE segment_id=? AND value_num IS NOT NULL
                          AND time_sort_num IS NOT NULL AND ''' +
                         ('label_int IS NULL' if score_only else 'label_int IN (0,1)'),
                    (segs[0]['id'],)).fetchone()
                if (bounds['start_s'] is None or bounds['end_s'] is None or
                        not all(math.isfinite(x) for x in bounds)):
                    raise ValueError(f'{run["model_alias"]} 的 {key} 没有有效采样，请调整筛选条件')
                entries.append({'source_id': run['source_id'], 'model_alias': run['model_alias'],
                                'feature_key': key, 'start_s': bounds['start_s'],
                                'end_s': bounds['end_s']})
    start = max(item['start_s'] for item in entries)
    end = min(item['end_s'] for item in entries)
    return {'role': role, 'source_ids': ids, 'feature_keys': features,
            'start_s': start, 'end_s': end, 'has_common_window': start <= end,
            'coverage': entries, 'time_axis': 'per_run_elapsed_seconds_displayed_as_calendar'}


def _selected(conn, ids, role):
    p=','.join('?'*len(ids))
    rows=[]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_annotations'").fetchone():
        rows=[dict(r) for r in conn.execute(f'''SELECT a.*,s.source_sha256
            FROM v2_run_annotations a JOIN v2_sources s ON a.source_id=s.id
            WHERE a.source_id IN ({p})''',ids)]
        for r in rows:r['data_role']='simulation'
    existing={r['source_id'] for r in rows}
    external={r['source_id']:r for r in _uploaded_runs(conn)}
    for sid in ids:
        if sid in existing:continue
        if sid in external:
            item=dict(external[sid])
            item['source_sha256']=conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?',(sid,)).fetchone()[0]
            rows.append(item)
    runs={r['source_id']:r for r in rows}
    if set(runs)!=set(ids):
        raise ValueError('选中了不存在、未明确声明用途或时间单位不符的运行')
    for r in rows:
        allowed = ((r['split']=='train' and r['run_role']=='NORMAL') if role=='train' else
            (r['split'] in HELDOUT_SPLITS if r['data_role']=='simulation' else r['split'] in (*HELDOUT_SPLITS,'test')))
        if not allowed:
            raise ValueError('运行划分不符合防泄漏约束：训练仅正常，测试仅留出/显式测试源')
        if r['metadata_not_model_features']!=1 or r['annotation_scope'] not in ('simulation_run','external_import'):
            raise ValueError('运行标签来源契约无效')
    ordered=[runs[sid] for sid in ids]
    if role=='test' and len({r['split'] for r in ordered})>1:
        raise ValueError('评估只能使用同一种数据划分；validation、confirmation、OOD 必须分开报告')
    return ordered


def _pick(series, keys, time, method, tolerance):
    if method=='exact':
        idx=bisect_left(keys,time)
        return series[idx] if idx<len(keys) and keys[idx]==time else None
    if method=='previous':
        idx=bisect_right(keys,time)-1
        return series[idx] if idx>=0 and time-keys[idx]<=tolerance else None
    idx=bisect_left(keys,time)
    options=(i for i in (idx-1,idx) if 0<=i<len(keys))
    closest=min(options,key=lambda i:(abs(keys[i]-time),keys[i]),default=None)
    return series[closest] if closest is not None and abs(keys[closest]-time)<=tolerance else None


def load_selection(db_path:Path, *, selection:dict, role:str, expected:dict|None=None, label_target:dict|None=None):
    if isinstance(selection,dict) and selection.get('coordinate_mode')=='frame':
        from scripts.server.v2_frame_dataset import load_frame_selection
        from scripts.server.v2_label_targets import normalize_target
        target=normalize_target(label_target)
        if target['scope']!='run':
            raise ValueError('帧 CSV 目前仅支持原始行级 0/1 真值，目标级标签须单独复核')
        return load_frame_selection(db_path,selection=selection,role=role,expected=expected)
    ids,features,start,end,method,tolerance=_parse_selection(selection,role=role)
    from scripts.server.v2_label_targets import normalize_target, target_for_run
    target=normalize_target(label_target)
    if role == 'train' and target['scope'] != 'run':
        raise ValueError('正常训练只读取明确的正常运行标签；目标级真值仅用于独立测试评估')
    if expected:
        if expected.get('feature_keys')!=features:
            raise ValueError('评估特征字段或顺序与已发布模型不符')
        train_ids=set(expected.get('train_source_ids') or [])
        if role=='test' and train_ids.intersection(ids):
            raise ValueError('训练和测试存在相同仿真运行，禁止数据泄漏')
    rows=[]; evidence=[]; seen_groups=set(); unknown_total=0; original_aligned=0
    with open_database(db_path) as conn:
        runs=_selected(conn,ids,role)
        with conn:  # query-only, no writes
            for run in runs:
                group=run['group_id']
                if group in seen_groups or (expected and role=='test' and group in expected.get('train_groups',[])):
                    raise ValueError('同一仿真组不能跨训练/测试或重复选择')
                if expected and role=='test' and run.get('source_sha256') in set(expected.get('train_source_digests') or []):
                    raise ValueError('测试数据与模型训练数据具有相同原始文件 SHA-256，禁止重复使用')
                seen_groups.add(group)
                blocks=[]; sem=[]; source_digests=[]; segment_ids=[]; feature_quality=[]
                for key in features:
                    segs=conn.execute('''SELECT g.id,c.subsystem,c.device,c.variable,c.unit,g.time_domain
                        FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                        WHERE g.source_id=? AND
                        ((?='simulation' AND g.source_table=?) OR
                         (?!='simulation' AND (c.subsystem || '.' || c.device || '.' || c.variable)=?))
                        AND g.kind='telemetry' AND g.usable_for_timeseries=1 AND g.role=? ''',
                        (run['source_id'],run['data_role'],key,run['data_role'],key,run['data_role'])).fetchall()
                    if len(segs)!=1 or segs[0]['time_domain']!='numeric_unspecified':
                        raise ValueError(f'运行 {run["model_alias"]} 缺少合规的参量 {key}')
                    seg=segs[0]
                    segment_ids.append(seg['id'])
                    sem.append(tuple(seg[x] for x in ('subsystem','device','variable','unit')))
                    sql='''SELECT time_sort_num AS t,value_num AS v,label_int AS label,source_ordinal AS ord
                        FROM v2_samples WHERE segment_id=?'''
                    args=[seg['id']]
                    if start is not None:sql+=' AND time_sort_num>=?';args.append(start)
                    if end is not None:sql+=' AND time_sort_num<=?';args.append(end)
                    sql+=' ORDER BY time_sort_num,source_ordinal LIMIT ?';args.append(MAX_ALIGNED+1)
                    items=[dict(r) for r in conn.execute(sql,args)]
                    if len(items)>MAX_ALIGNED:raise ValueError('单参量超过安全读取上限，请缩小时间范围')
                    if not items: raise ValueError(f'{run["model_alias"]} 的 {key} 在该时间范围内无采样')
                    prev=None; hash_obj=hashlib.sha256();valid=[];invalid=0
                    for r in items:
                        if r['t'] is None or not math.isfinite(r['t']) or r['label'] not in (0,1):
                            raise ValueError('采样包含无效时间或标签')
                        if prev is not None and r['t']<=prev:
                            raise ValueError('同一运行参量出现重复或倒退的时间戳')
                        prev=r['t']
                        hash_obj.update(f"{r['t']!r}|{r['v']!r}|{r['label']}|{r['ord']}\n".encode())
                        if r['v'] is None or not math.isfinite(r['v']):
                            invalid+=1
                        else:
                            valid.append(r)
                    if not valid:raise ValueError(f'{run["model_alias"]} 的 {key} 在该时间范围内没有有效样本')
                    feature_quality.append({'feature_key':key,'total':len(items),
                                            'valid_samples':len(valid),'invalid_samples':invalid})
                    # Keep NULL timestamps as alignment barriers: previous/nearest
                    # must never jump over an unfilled gap to an older value.
                    blocks.append(items);source_digests.append(hash_obj.hexdigest())
                from scripts.server.telemetry_v2_access import _clock_contracts
                reference,unit=_clock_contracts(conn,segment_ids,'numeric_unspecified')
                if unit!='s' or not reference or (run['data_role']=='simulation' and not reference.startswith('elapsed_run:')):
                    raise ValueError('必须验证各运行独立的 time_s 秒时间基准')
                ref_sem=sem
                if evidence and evidence[0]['semantic_features']!=[list(x) for x in ref_sem]:
                    raise ValueError('不同运行中所选参量的分系统/部件语义不一致')
                classify, label_evidence = target_for_run(conn, run, target, features, sem, segment_ids)
                extras=[([x['t'] for x in block],block) for block in blocks[1:]]
                run_count=0; unknown_run=0; block_id=0; counts={'normal':0,'fault':0}
                for anchor in blocks[0]:
                    if anchor['v'] is None or not math.isfinite(anchor['v']):
                        continue
                    selected=[anchor]
                    for keys,block in extras:
                        found=_pick(block,keys,anchor['t'],method,tolerance)
                        if found is None or found['v'] is None or not math.isfinite(found['v']):break
                        selected.append(found)
                    if len(selected)!=len(features):continue
                    if any(x['label']!=anchor['label'] for x in selected):
                        raise ValueError('跨参量标签不一致，不能通过 OR/覆盖强行合并')
                    original_aligned+=1
                    derived_label=anchor['label'] if classify is None else classify(anchor['t'])
                    if derived_label is None:
                        unknown_total+=1;unknown_run+=1;block_id+=1
                        continue
                    if derived_label not in (0,1):raise ValueError('评估目标标签必须为 0/1/未知')
                    if role=='train' and anchor['label']!=0:
                        raise ValueError('正常运行训练数据中发现故障标签')
                    row={'timestamp':f"{run['model_alias']}@{anchor['t']:.9g}",
                         'time_s':anchor['t'],'run_source_id':run['source_id'],
                         'label':derived_label,'label_block_id':block_id,'value':anchor['v']}
                    for k,x in enumerate(selected[1:],2):row[f'source_{k}']=x['v']
                    rows.append(row);run_count+=1;counts['fault' if row['label'] else 'normal']+=1
                    if len(rows)>MAX_ALIGNED:raise ValueError('总对齐样本数超过上限，请缩小时间范围或减少运行数')
                if not run_count and unknown_run==0:raise ValueError(f'{run["model_alias"]} 所选区间和参量没有可对齐样本')
                evidence.append({'source_id':run['source_id'],'group_id':group,'model_alias':run['model_alias'],
                    'split':run['split'],'run_role':run['run_role'],'count':run_count,
                    'normal_count':counts['normal'],'fault_count':counts['fault'],
                    'target_sha256':run['target_sha256'],'source_sha256':run['source_sha256'],
                    'data_role':run['data_role'],
                    'time_reference':reference,
                    'feature_digests':source_digests,'semantic_features':[list(x) for x in sem],
                    'sample_quality':feature_quality,'excluded_unknown_labels':unknown_run,
                    'label_evidence':label_evidence})
    if not rows:raise ValueError('选定评估目标没有任何已确认标签的有效样本，禁止计算指标')
    if role=='test' and target['scope']!='run' and {r['label'] for r in rows}!={0,1}:
        raise ValueError('该评估目标需要明确的正常和故障真值，不能将未知标签自动视为正常')
    contract={'schema':'telemetry-v2-run-subset','role':role,'feature_keys':features,
         'feature_columns':['value']+[f'source_{i}' for i in range(2,len(features)+1)],
         'time_window':{'start':start,'end':end},'method':method,'tolerance_seconds':tolerance,
         'runs':evidence,'aligned_count':len(rows),'normal_count':sum(x['normal_count'] for x in evidence),
         'fault_count':sum(x['fault_count'] for x in evidence),
         'sample_check':{'aligned_valid_samples':len(rows),
             'unfilled_or_invalid_source_samples':sum(f['invalid_samples'] for run in evidence for f in run['sample_quality']),
             'per_run':[{'source_id':r['source_id'],'aligned_valid_samples':r['count'],
                         'per_feature':r['sample_quality']} for r in evidence]},
         'label_policy':('simulation_run_truth_or_user_explicit_import_labels' if target['scope']=='run' else 'explicit_'+target['scope']+'_truth_unknown_excluded'),
         'label_target':target,'evaluation_split':runs[0]['split'] if role=='test' else None,
         'label_coverage':{'aligned_before_label_filter':original_aligned,
             'known':len(rows),'excluded_unknown':unknown_total},
         'time_axis':'per_run_numeric_seconds_never_join_across_runs'}
    # Bind the exact column layout (key order + physical semantics) that was
    # actually read from the database. semantic_features is already proven equal
    # across every selected run above, so run 0 is representative.
    attest_feature_schema(contract, keys=features,
                          semantics=evidence[0]['semantic_features'] if evidence else None)
    if expected and role=='train' and expected.get('train_run_evidence')!=evidence:
        raise ValueError('训练数据或目标标签自任务提交后发生变化')
    if expected:
        assert_feature_schema_matches(expected.get('feature_schema'), contract['feature_schema'])
    return rows,contract



def preview_plot_series(rows, contract, *, max_points=1600):
    """Bounded display-only plot samples. Never concatenate independent runs.

    A chart point contains only values already selected/aligned under the V2
    training/test contract. It does not change the training input or labels.
    """
    if type(max_points) is not int or not 50 <= max_points <= 4000:
        raise ValueError('预览绘图点数限制无效')
    features=contract['feature_keys']
    groups={run['source_id']:[] for run in contract['runs']}
    for row in rows:
        if row['run_source_id'] not in groups:
            raise ValueError('绘图数据来源与输入契约不一致')
        groups[row['run_source_id']].append(row)
    per_run=max(2,max_points // max(1,len(groups)))
    result=[]
    for run in contract['runs']:
        run_rows=groups[run['source_id']]
        if not run_rows:
            continue
        # Deterministic ordering and downsampling; retain the last sample.
        if len(run_rows)<=per_run:
            positions=range(len(run_rows))
        else:
            positions=[round(i*(len(run_rows)-1)/(per_run-1)) for i in range(per_run)]
        points=[]
        for idx in positions:
            row=run_rows[idx]
            values=[row['value']]+[row[f'source_{i}'] for i in range(2,len(features)+1)]
            points.append({'time_s':row.get('time_s'),'frame_index':row.get('frame_index'),
                           'coordinate_mode':contract.get('coordinate_mode','time'),
                           'label':row.get('label'),'values':values})
        result.append({'source_id':run['source_id'],'model_alias':run['model_alias'],
                       'original_count':len(run_rows),'plotted_count':len(points),'points':points})
    return result


def validate_training_selection(db_path, payload):
    if not isinstance(payload,dict):raise ValueError('训练配置必须为对象')
    allowed={'dataset_selection','algorithm','algorithm_params','train_ratio','normalization_method','training_strategy'}
    if set(payload)-allowed:raise ValueError('旧数据表选择已停用，不支持未知训练配置')
    ratio=payload.get('train_ratio',0.8)
    if isinstance(ratio,bool) or not isinstance(ratio,(int,float)) or not 0.5<=ratio<1:
        raise ValueError('内部训练比例需要在 [0.5,1)')
    _,meta=load_selection(db_path,selection=payload.get('dataset_selection'),role='train')
    min_samples=8 if len(meta['runs'])==1 else 4
    if meta['aligned_count']<min_samples:
        raise ValueError('正常训练数据不足，单运行须留出时间隔离间隔')
    return meta
