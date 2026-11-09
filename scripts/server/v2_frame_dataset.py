"""Run-isolated frame alignment for training, supervised evaluation and inference."""
from __future__ import annotations

import hashlib
import math

from scripts.server.feature_schema import attest_feature_schema, assert_feature_schema_matches
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_training import MAX_ALIGNED


def load_frame_selection(db_path, *, selection, role, expected=None, score_only=False):
    from scripts.server.v2_run_dataset import _parse_selection, _uploaded_runs
    ids,keys,start,end,method,tolerance=_parse_selection(selection,role=role)
    if selection.get('coordinate_mode')!='frame':
        raise ValueError('缺少帧坐标模式')
    if score_only and (role!='test' or len(ids)!=1):
        raise ValueError('无标签推理一次只接受一个测试 CSV')
    if expected:
        if expected.get('feature_keys')!=keys:
            raise ValueError('模型参量字段或顺序不一致')
        if expected.get('coordinate_mode','time')!='frame':
            raise ValueError('模型坐标模式与帧测试数据不兼容')
        if role=='test' and set(ids)&set(expected.get('train_source_ids') or []):
            raise ValueError('测试来源与训练来源相同')
    rows=[]; evidence=[]; seen_groups=set(); semantics_first=None
    with open_database(db_path) as conn:
        available={run['source_id']:run for run in _uploaded_runs(conn,include_unlabeled=score_only)}
        for sid in ids:
            run=available.get(sid)
            if not run or run['coordinate_mode']!='frame' or run['frame_sequence_id']!=selection['frame_sequence_id']:
                raise ValueError('来源不存在或帧序列 ID 不匹配')
            if run['allowed_role']!=('score_only' if score_only else role):
                raise ValueError('来源用途或标签状态不符合当前任务')
            if run['group_id'] in seen_groups or (expected and run['group_id'] in (expected.get('train_groups') or [])):
                raise ValueError('同组运行不能重复或跨训练/测试')
            seen_groups.add(run['group_id'])
            source=conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?',(sid,)).fetchone()
            digest=source['source_sha256']
            if expected and digest in (expected.get('train_source_digests') or []):
                raise ValueError('测试文件与训练 CSV 摘要相同')
            blocks=[];semantics=[];quality=[];digests=[]
            for key in keys:
                segments=conn.execute('''SELECT g.id,c.subsystem,c.device,c.variable,c.unit
                    FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
                    WHERE g.source_id=? AND g.frame_sequence_id=? AND g.coordinate_mode='frame'
                    AND g.role=? AND (c.subsystem||'.'||c.device||'.'||c.variable)=?''',
                    (sid,selection['frame_sequence_id'],role,key)).fetchall()
                if len(segments)!=1:
                    raise ValueError('帧序列缺少唯一参量：'+key)
                segment=segments[0]
                semantics.append([segment[name] for name in ('subsystem','device','variable','unit')])
                sql='SELECT frame_index AS frame,value_num AS v,label_int AS label,source_ordinal AS ord FROM v2_samples WHERE segment_id=?'
                args=[segment['id']]
                if start is not None:sql+=' AND frame_index>=?';args.append(start)
                if end is not None:sql+=' AND frame_index<=?';args.append(end)
                sql+=' ORDER BY frame_index LIMIT ?';args.append(MAX_ALIGNED+1)
                items=[dict(item) for item in conn.execute(sql,args)]
                if not items or len(items)>MAX_ALIGNED:
                    raise ValueError('帧范围内没有采样或超过读取上限')
                invalid=0;previous=None;missing_frames=0;hasher=hashlib.sha256()
                for item in items:
                    position=item['frame']
                    if type(position) is not int or position<1 or previous is not None and position<=previous:
                        raise ValueError('帧号重复或乱序')
                    if previous is not None:missing_frames+=position-previous-1
                    previous=position
                    if (item['label'] is not None) if score_only else (item['label'] not in (0,1)):
                        raise ValueError('帧真值状态与任务不符')
                    if role=='train' and item['label']!=0:
                        raise ValueError('正常训练集包含故障标签')
                    if item['v'] is None or not math.isfinite(item['v']):invalid+=1
                    hasher.update(f"{position}|{item['v']!r}|{item['label']!r}|{item['ord']}\n".encode())
                if start is None:
                    missing_frames+=items[0]['frame']-1
                if role=='train' and missing_frames:
                    raise ValueError('训练帧序列存在缺失帧，须先纠错')
                if invalid==len(items) or invalid/len(items)>0.5:
                    raise ValueError('参量缺测率超过 50% 或没有有效值：'+key)
                blocks.append({item['frame']:item for item in items})
                quality.append({'feature_key':key,'total':len(items),'invalid_samples':invalid,
                                'missing_frames':missing_frames,'missing_rate':invalid/len(items)})
                digests.append(hasher.hexdigest())
            if semantics_first is None:semantics_first=semantics
            elif semantics!=semantics_first:
                raise ValueError('多运行参量语义或单位不一致')
            if expected:
                from scripts.server.feature_schema import build_feature_schema
                assert_feature_schema_matches(expected.get('feature_schema'),build_feature_schema(keys,semantics))
            if role=='train' and any(set(block)!=set(blocks[0]) for block in blocks[1:]):
                raise ValueError('训练参量帧集合不一致，不能静默丢失采样位置')
            common_frames=set(blocks[0])
            for block in blocks[1:]:common_frames &= set(block)
            count=0;normal=fault=0
            for frame in sorted(common_frames):
                selected=[block[frame] for block in blocks]
                if any(item['label']!=selected[0]['label'] for item in selected):
                    raise ValueError('同一帧各参量标签不一致')
                if any(item['v'] is None for item in selected):continue
                row={'timestamp':f"{run['model_alias']}#frame{frame}",
                     'time_s':None,'frame_index':frame,'run_source_id':sid,'value':selected[0]['v']}
                row.update({f'source_{i}':item['v'] for i,item in enumerate(selected[1:],2)})
                if not score_only:
                    row['label']=selected[0]['label'];row['label_block_id']=0
                    normal+=row['label']==0;fault+=row['label']==1
                rows.append(row);count+=1
                if len(rows)>MAX_ALIGNED:raise ValueError('总对齐帧数超过安全上限')
            if not count:raise ValueError('多参量没有有效的共同帧')
            if role=='test' and expected and expected.get('train_source_ids'):
                test_rows=[row for row in rows if row['run_source_id']==sid]
                for train_sid in expected['train_source_ids']:
                    comparisons=[]
                    for identity in semantics:
                        matched=conn.execute('''SELECT g.id FROM v2_segments g
                            JOIN v2_channels c ON c.id=g.channel_id
                            WHERE g.source_id=? AND g.coordinate_mode='frame'
                            AND c.subsystem=? AND c.device=? AND c.variable=? AND c.unit=?''',
                            (train_sid,*identity)).fetchall()
                        if len(matched)!=1:
                            raise ValueError('模型训练来源的帧参量无法复核')
                        comparisons.append({row['frame_index']:row['value_num'] for row in conn.execute(
                            'SELECT frame_index,value_num FROM v2_samples WHERE segment_id=?',
                            (matched[0]['id'],))})
                    identical=sum(all(block.get(row['frame_index'])==row['value' if i==0 else f'source_{i+1}']
                                      and row['frame_index'] in block for i,block in enumerate(comparisons))
                                  for row in test_rows)
                    if identical>=min(2,len(test_rows)):
                        raise ValueError('测试 CSV 与训练来源存在重复遥测帧')
            evidence.append({'source_id':sid,'group_id':run['group_id'],'model_alias':run['model_alias'],
                'split':run['split'],'run_role':run['run_role'],'count':count,
                'normal_count':normal,'fault_count':fault,'target_sha256':None,
                'source_sha256':digest,'data_role':role,'coordinate_mode':'frame',
                'frame_sequence_id':run['frame_sequence_id'],'feature_digests':digests,
                'semantic_features':semantics,'sample_quality':quality,
                'label_evidence':'csv_explicit' if not score_only else 'unknown'})
    contract={'schema':'telemetry-v2-run-subset','role':role,'feature_keys':keys,
        'feature_columns':['value']+[f'source_{i}' for i in range(2,len(keys)+1)],
        'coordinate_mode':'frame','coordinate_contract':{'mode':'frame','frame_origin':1,
            'alignment':'frame_index','frame_sequence_policy':'independent'},
        'time_window':{'start':start,'end':end},'method':'exact','tolerance_seconds':0,
        'runs':evidence,'aligned_count':len(rows),'normal_count':sum(r['normal_count'] for r in evidence),
        'fault_count':sum(r['fault_count'] for r in evidence),
        'sample_check':{'aligned_valid_samples':len(rows),
            'unfilled_or_invalid_source_samples':sum(q['invalid_samples'] for run in evidence for q in run['sample_quality']),
            'per_run':[{'source_id':r['source_id'],'aligned_valid_samples':r['count'],'per_feature':r['sample_quality']} for r in evidence]},
        'label_policy':'unlabeled_unknown_not_normal' if score_only else 'user_explicit_import_labels',
        'label_target':{'scope':'run'},'evaluation_split':evidence[0]['split'] if role=='test' else None,
        'label_coverage':{'known':0 if score_only else len(rows),'unknown':len(rows) if score_only else 0},
        'time_axis':'frame_index_not_time','ground_truth_available':not score_only}
    attest_feature_schema(contract,keys=keys,semantics=semantics_first)
    if expected and role=='train' and expected.get('train_run_evidence')!=evidence:
        raise ValueError('训练数据自提交后变化')
    if expected:
        assert_feature_schema_matches(expected.get('feature_schema'),contract['feature_schema'])
    return rows,contract
