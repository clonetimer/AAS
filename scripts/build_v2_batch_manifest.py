#!/usr/bin/env python3
"""Generate REVIEWABLE batch input manifest using verified targets.zip records.

Model denotes a user-confirmed dataset satellite family; tokens distinguish
actual independent runs. No per-channel truth is invented. Unknown units require
explicit human overrides and must not be silently guessed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
from scripts.target_archive_evidence import read_targets

TOKEN_FILENAME=re.compile(r'^([0-9a-f]{24})\.csv(?:\(\d+\))?(?:\.gz)?$', re.I)


def build_plan(source_dir,targets_zip,*,model,unit_overrides=None):
    base=Path(source_dir).resolve()
    if not base.is_dir():raise ValueError('遥测目录不存在')
    if not model or not model.strip() or len(model)>100:raise ValueError('必须明确提供数据集卫星型号/家族名')
    targets=read_targets(targets_zip)
    unit_overrides=unit_overrides or {}
    if not isinstance(unit_overrides,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() for k,v in unit_overrides.items()):
        raise ValueError('单位覆写需要字典形式的字符串参量名和单位')
    catalog=json.loads((Path(__file__).resolve().parents[1]/'web/visualization/telemetry-v2-column-catalog.json').read_text(encoding='utf-8'))['items']
    # Unit overrides are explicit review decisions; no made-up physical units.
    replacements={name:{'unit':unit} for name,unit in unit_overrides.items()}
    files=[];excluded=[];unmatched=[];duplicates=set();seen_tokens=set()
    for path in sorted(base.iterdir()):
        if not path.is_file():continue
        m=TOKEN_FILENAME.fullmatch(path.name)
        if not m:
            if path.name.lower().endswith(('.csv','.csv.gz')):unmatched.append(path.name)
            continue
        token=m[1].lower()
        if token in seen_tokens:
            duplicates.add(token)
            continue
        seen_tokens.add(token)
        evidence=targets.get(token)
        if evidence is None:
            unmatched.append(path.name)
            continue
        target=evidence['target']
        if target['split']=='train' and target['role']=='FAULT':
            excluded.append({'filename':path.name,'reason':'original_train_FAULT_not_normal_fitting'})
            continue
        role='train' if target['split']=='train' else 'test'
        cfg={'namespace':token,'model':model.strip(),'satellite_instance':'',
             'subsystem':'_column_mapping_','device':'_column_mapping_','unit':'unknown',
             'role':role,'time_domain':'numeric_unspecified',
             'time_reference':'elapsed_run:'+token,'time_unit':'s',
             'auto_map':True,'column_metadata':replacements,
             'unlabeled_test_confirmed':True,
             'import_context':{'run_token':token,'dataset_split':target['split'],
                               'group_id':target['group'],'run_role':target['role'],
                               'origin_type':'simulation','label_scope':'run',
                               'target_metadata':target}}
        if target['role']=='NORMAL' and role=='train':
            # No labels in uploaded 56-channel telemetry; this must be a
            # reviewed assumption, never set automatically from filename.
            cfg['all_normal_confirmed']=False
        files.append({'path':path.name,'config':cfg})
    if duplicates:
        raise ValueError('同一运行具有多个本地文件，请人工消歧：'+', '.join(sorted(duplicates)))
    return {'schema_version':1,'files':files}, {'found':len(files),'unmatched':unmatched,
           'excluded_train_fault':excluded,
           'pending_review':'检查每项 model、缺失单位，并在确认训练文件逐点正常后将 all_normal_confirmed 设置为 true',
           'verified_targets_archive_members':len(targets),
           'targets_scope':'run_only_no_parameter_truth'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,required=True)
    p.add_argument('--targets-zip',type=Path,required=True)
    p.add_argument('--model',required=True)
    p.add_argument('--unit-overrides',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    overrides=json.loads(args.unit_overrides.read_text(encoding='utf-8')) if args.unit_overrides else None
    plan,report=build_plan(args.source_dir,args.targets_zip,model=args.model,unit_overrides=overrides)
    # Manifest must live in same directory as telemetry sources for path anchoring.
    if args.output.resolve().parent!=args.source_dir.resolve():
        p.error('输出清单必须位于遥测目录内，以确保引用路径可审计')
    args.output.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
