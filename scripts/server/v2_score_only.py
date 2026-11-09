"""Read-only, run-isolated unknown-label sample selection for published model inference.

This intentionally does not call the supervised V2 loader or metric engine.
It only accepts explicitly imported test CSV data with SQL NULL labels.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from scripts.server.feature_schema import build_feature_schema, assert_feature_schema_matches
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_training import MAX_ALIGNED
from scripts.server.v2_run_dataset import _parse_selection, _uploaded_runs, _pick


def _reject_overlapping_training_frames(conn, *, test_rows, semantics, expected, method, tolerance):
    """Compare actual telemetry frames, independent of CSV labels and file bytes.

    The published manifest's training sources must still be verifiable in the
    managed database. Distinct file hashes or digest-bound groups do not prove
    disjointness: removing a label column changes both without changing data.
    Repeated complete aligned frames are rejected conservatively; a single
    matching initial state in longer independent runs is not by itself proof
    that one dataset was copied from another.
    """
    train_ids=expected.get('train_source_ids') or []
    if not train_ids:
        return {'status':'not_applicable','reason':'no_training_source_ids'}
    digests=expected.get('train_source_digests') or []
    if (not isinstance(train_ids,list) or any(type(sid) is not int or sid<=0 for sid in train_ids)
            or not isinstance(digests,list) or any(not isinstance(x,str) or len(x)!=64 for x in digests)):
        raise ValueError('训练来源元数据不完整，无法排除训练/测试样本泄漏')
    if len(set(train_ids))!=len(train_ids) or len(digests)<len(train_ids):
        raise ValueError('训练来源摘要缺失，无法完成重复样本校验')
    begin=min(row['time_s'] for row in test_rows)-tolerance
    finish=max(row['time_s'] for row in test_rows)+tolerance
    names=['value']+[f'source_{i}' for i in range(2,len(semantics)+1)]
    for sid in train_ids:
        source=conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?',(sid,)).fetchone()
        if source is None or source['source_sha256'] not in digests:
            raise ValueError('模型训练来源已缺失或数据摘要变化，无法验证训练/测试独立性')
        blocks=[]
        for identity in semantics:
            matched=conn.execute('''SELECT g.id FROM v2_segments g
                JOIN v2_channels c ON c.id=g.channel_id
                WHERE g.source_id=? AND g.kind='telemetry'
                  AND g.usable_for_timeseries=1
                  AND c.subsystem=? AND c.device=? AND c.variable=? AND c.unit=?''',
                (sid,*identity)).fetchall()
            if len(matched)!=1:
                raise ValueError('模型训练来源的物理参量无法复核，禁止无标签推理')
            samples=conn.execute('''SELECT time_sort_num AS t,value_num AS v
                FROM v2_samples WHERE segment_id=? AND time_sort_num BETWEEN ? AND ?
                ORDER BY time_sort_num,source_ordinal LIMIT ?''',
                (matched[0]['id'],begin,finish,MAX_ALIGNED+1)).fetchall()
            if len(samples)>MAX_ALIGNED:
                raise ValueError('训练来源采样量超过重复检查上限，请缩小测试时间范围')
            times=[sample['t'] for sample in samples]
            if any(t is None or not math.isfinite(t) for t in times) or any(
                    a>=b for a,b in zip(times,times[1:])):
                raise ValueError('训练来源时间戳异常，无法验证数据独立性')
            blocks.append((times,samples))
        overlap_count=0
        for row in test_rows:
            values=[_pick(samples,times,row['time_s'],method,tolerance)
                    for times,samples in blocks]
            if all(value is not None and value['v'] is not None and
                   math.isfinite(value['v']) and value['v']==row[name]
                   for value,name in zip(values,names)):
                overlap_count+=1
                if overlap_count >= min(2,len(test_rows)):
                    raise ValueError('测试 CSV 与模型训练来源存在重复遥测帧，禁止数据泄漏')
    return {'status':'verified_no_exact_aligned_frame_overlap',
            'checked_training_sources':len(train_ids),'checked_test_frames':len(test_rows)}


def load_score_only_rows(db_path: Path, *, selection: dict, expected: dict):
    """Return (ordered numeric input rows, provenance) without synthesizing truths."""
    if isinstance(selection,dict) and selection.get('coordinate_mode')=='frame':
        from scripts.server.v2_frame_dataset import load_frame_selection
        rows,contract=load_frame_selection(db_path,selection=selection,role='test',
            expected=expected,score_only=True)
        contract['training_overlap_check']={'status':'independent_source_and_digest_checked',
            'checked_training_sources':len(expected.get('train_source_ids') or [])}
        return rows,contract
    ids, keys, start, end, method, tolerance = _parse_selection(selection, role='test')
    if len(ids) != 1:
        raise ValueError('无标签仅推理每次只能选择一个明确的测试来源')
    if not isinstance(expected, dict) or expected.get('feature_keys') != keys:
        raise ValueError('评估特征及顺序必须与已发布模型一致')
    saved_semantics=(expected.get('feature_schema') or {}).get('semantics')
    if (not isinstance(saved_semantics,list) or len(saved_semantics)!=len(keys) or
            any(not isinstance(item,list) or len(item)!=4 or
                any(not isinstance(value,str) or not value for value in item)
                for item in saved_semantics)):
        raise ValueError('仅推理模型缺少完整的特征物理语义绑定')
    sid = ids[0]
    if sid in (expected.get('train_source_ids') or []):
        raise ValueError('训练来源不能作为未知真值测试来源，防止数据泄漏')
    rows = []
    semantics = []
    feature_quality = []
    with open_database(db_path) as conn:
        runs = {run['source_id']: run for run in _uploaded_runs(conn, include_unlabeled=True)}
        run = runs.get(sid)
        if run is None or run['allowed_role'] != 'score_only':
            raise ValueError('仅推理只接受明确为测试用途、全部标签未知的导入 CSV')
        if run['group_id'] in (expected.get('train_groups') or []):
            raise ValueError('所选测试来源与模型训练运行同组，禁止数据泄漏')
        source = conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?', (sid,)).fetchone()
        digest = source['source_sha256'] if source else None
        if not digest or digest in (expected.get('train_source_digests') or []):
            raise ValueError('所选 CSV 原始文件与训练数据相同或缺少身份摘要，禁止数据泄漏')
        blocks = []
        clocks = []
        for key,identity in zip(keys,saved_semantics):
            matching = conn.execute('''SELECT g.id,c.subsystem,c.device,c.variable,c.unit,
                    g.time_domain,k.reference,k.unit AS clock_unit
                FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                LEFT JOIN v2_segment_clock k ON k.segment_id=g.id
                WHERE g.source_id=? AND g.role='test' AND g.kind='telemetry'
                  AND g.usable_for_timeseries=1
                  AND c.subsystem=? AND c.device=? AND c.variable=? AND c.unit=?''',
                (sid,*identity)).fetchall()
            if len(matching) != 1 or matching[0]['time_domain'] != 'numeric_unspecified':
                raise ValueError('测试来源缺少与模型特征物理语义或单位匹配的参量：'+key)
            segment=matching[0]
            if segment['clock_unit'] != 's' or not segment['reference']:
                raise ValueError('测试 CSV 缺少明确的运行秒时间基准')
            clocks.append(segment['reference'])
            semantics.append([segment[x] for x in ('subsystem','device','variable','unit')])
            sql='''SELECT time_sort_num AS t,value_num AS v,label_int AS label,source_ordinal AS ord
                FROM v2_samples WHERE segment_id=?'''
            params=[segment['id']]
            if start is not None:
                sql+=' AND time_sort_num>=?';params.append(start)
            if end is not None:
                sql+=' AND time_sort_num<=?';params.append(end)
            sql+=' ORDER BY time_sort_num,source_ordinal LIMIT ?';params.append(MAX_ALIGNED+1)
            values=[dict(row) for row in conn.execute(sql,params)]
            if len(values)>MAX_ALIGNED:raise ValueError('单参量采样超过安全上限，请缩小时间区间')
            if not values:raise ValueError('所选测试来源在选定时间段内没有参量采样：'+key)
            previous=None; invalid=0;hasher=hashlib.sha256()
            for item in values:
                moment=item['t']; value=item['v']
                if moment is None or not math.isfinite(moment) or item['label'] is not None:
                    raise ValueError('仅推理数据含有无效时间或真实标签，禁止将混合标签用于未知真值推理')
                if previous is not None and moment<=previous:
                    raise ValueError('同一来源参量存在重复或倒退时间戳')
                previous=moment
                hasher.update(f"{moment!r}|{value!r}|{item['ord']!r}\n".encode())
                if value is None or not math.isfinite(value):invalid+=1
            feature_quality.append({'feature_key':key,'total':len(values),
                                    'invalid_samples':invalid,'sha256':hasher.hexdigest()})
            blocks.append(values)
        if len(set(clocks)) != 1:
            raise ValueError('多参量的运行时间基准不一致')
        assert_feature_schema_matches(expected.get('feature_schema'),
                                      build_feature_schema(keys, semantics))
        extras=[([item['t'] for item in block],block) for block in blocks[1:]]
        for item in blocks[0]:
            if item['v'] is None or not math.isfinite(item['v']):continue
            matched=[item]
            for times,block in extras:
                value=_pick(block,times,item['t'],method,tolerance)
                if value is None or value['v'] is None or not math.isfinite(value['v']):break
                matched.append(value)
            if len(matched)!=len(keys):continue
            row={'timestamp':f"{run['model_alias']}@{item['t']:.9g}",
                 'time_s':item['t'],'run_source_id':sid,'value':item['v']}
            row.update({f'source_{i}':entry['v'] for i,entry in enumerate(matched[1:],2)})
            rows.append(row)
            if len(rows)>MAX_ALIGNED:raise ValueError('仅推理对齐样本数超过安全上限')
        if not rows:raise ValueError('所选来源没有有效可对齐的模型输入帧')
        leak_check=_reject_overlapping_training_frames(conn,test_rows=rows,
            semantics=semantics,expected=expected,method=method,tolerance=tolerance)
    return rows, {'feature_keys':keys,'aligned_count':len(rows),'ground_truth_available':False,
        'time_window':{'start':start,'end':end},'method':method,'tolerance_seconds':tolerance,
        'runs':[{'source_id':sid,'model_alias':run['model_alias'],
                 'group_id':run['group_id'],'source_sha256':digest,
                 'time_reference':clocks[0],'semantic_features':semantics,
                 'sample_quality':feature_quality}],
        'label_policy':'unlabeled_unknown_not_normal','label_coverage':{'known':0,'unknown':len(rows)},
        'training_overlap_check':leak_check,
        'sample_check':{'aligned_valid_samples':len(rows),
          'unfilled_or_invalid_source_samples':sum(x['invalid_samples'] for x in feature_quality)}}


def score_with_published_detector(db_path: Path, *, selection: dict, expected: dict,
                                  scaling: dict, feature_columns: list[str],
                                  algorithm: str, detector):
    """Apply saved training-time scaling, then score unknown-label data once.

    Reuses the exact AAS AlgorithmRunner detector-owned decision implementation.
    The real model is restored by the existing authenticated published-model
    loader; this function cannot fit a model or compute ground-truth metrics.
    """
    from scripts.core.algorithm_runner import AlgorithmRunner
    from scripts.core.input_scaling import apply_scaler
    from scripts.core.input_evidence import capture_stage, finish_evidence
    from types import SimpleNamespace

    rows,contract=load_score_only_rows(db_path,selection=selection,expected=expected)
    names=['value']+[f'source_{i}' for i in range(2,len(contract['feature_keys'])+1)]
    if feature_columns!=names or not isinstance(scaling,dict) or scaling.get('enabled') is not True:
        raise ValueError('无标签推理需要与已发布模型一致的列序和训练期归一化契约')
    parameters=scaling.get('parameters')
    if not isinstance(parameters,dict) or not parameters:
        raise ValueError('模型缺少训练期归一化参数')
    if len(names)>1:
        per_feature=parameters.get('per_feature')
        if not isinstance(per_feature,dict) or list(per_feature)!=names:
            raise ValueError('模型归一化特征数量或列序不匹配')
        scaled=[apply_scaler([row[name] for row in rows],per_feature[name]) for name in names]
        matrix=[list(point) for point in zip(*scaled)]
    else:
        matrix=apply_scaler([row['value'] for row in rows],parameters)
    input_sha=hashlib.sha256(json.dumps(matrix,ensure_ascii=False,
                    separators=(',',':'),allow_nan=False).encode()).hexdigest()
    raw_matrix=([[row[name] for name in names] for row in rows] if len(names)>1
                else [row['value'] for row in rows])
    alignment={'method':contract['method'],'tolerance_seconds':contract['tolerance_seconds'],
               'time_window':contract['time_window'],'runs':contract['runs']}
    def evidence_bundle(values):
        return SimpleNamespace(train_values=[],train_value_rows=[],test_values=values,
            test_value_rows=rows,mode='score_only',metadata={'alignment':alignment})
    raw_evidence=capture_stage(evidence_bundle(raw_matrix),'raw',feature_columns)
    scaled_bundle=evidence_bundle(matrix)
    model_evidence=capture_stage(scaled_bundle,'model',feature_columns)
    input_evidence,input_snapshot=finish_evidence(raw_evidence,model_evidence,
        scaled_bundle,feature_columns,scaling)
    input_evidence.update({'model_input_sha256':input_sha,
        'source_sha256':contract['runs'][0]['source_sha256'],
        'aligned_count':len(rows),'ground_truth_available':False})
    outcome=AlgorithmRunner().run_preloaded(algorithm,matrix,detector,test_segments=[len(rows)])
    scores=[float(v) for v in outcome.raw_scores]
    if len(scores)!=len(rows) or any(not math.isfinite(v) for v in scores):
        raise ValueError('模型异常分数数量错误或包含 NaN/Inf')
    if len(outcome.pred_labels)!=len(rows) or any(value not in (0,1) for value in outcome.pred_labels):
        raise ValueError('模型故障判定数量与输入帧不一致')
    decision_threshold=(float(outcome.decision_threshold)
                        if outcome.decision_threshold is not None else None)
    if decision_threshold is not None and not math.isfinite(decision_threshold):
        raise ValueError('模型返回无效报警阈值（NaN/Inf）')
    thresholds=[None if v is None else float(v) for v in outcome.decision_thresholds]
    if (thresholds and len(thresholds)!=len(rows)) or any(
            v is not None and not math.isfinite(v) for v in thresholds):
        raise ValueError('模型逐帧报警阈值数量错误或含 NaN/Inf')
    result={'rows':rows,'threshold':scores,'pred_labels':list(outcome.pred_labels),
            'decision_mode':outcome.decision_mode,
            'decision_threshold':decision_threshold,
            'decision_thresholds':thresholds,
            'metrics':{},'ground_truth_available':False,
            'evaluation_mode':'score_only','label_policy':'unknown_no_metrics',
            'input_evidence':input_evidence}
    return {'status':'success','evaluation_mode':'score_only',
            'ground_truth_available':False,'result':result,
            'input_contract':contract,'input_snapshot':input_snapshot}
