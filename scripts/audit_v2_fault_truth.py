"""Phase 45: independently audit/run-scoped repair of the 19-run V2 fault map.

No inferred parameter events, no modification of v2_samples or original database.
`--apply-copy` requires source+target annotation fingerprints to match the
bundled 19-run evidence manifest; refuses conflicts and copies to a NEW DB.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing

from scripts.target_fault_location import FAULT_LOCATIONS
from scripts.target_archive_evidence import read_targets, check_annotated_database

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'docs' / 'AAS_19_models_and_fault_labels_20261020.csv'
CHANNEL_CATALOG = ROOT / 'docs' / 'AAS_56_telemetry_subsystems_components_20261020.csv'


def _csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def evidence_manifest(path=DEFAULT_MANIFEST):
    rows=_csv(path)
    by_token={}
    models=set()
    for row in rows:
        token=row['token']; model=row['model_alias']
        if token in by_token or model in models or row['target_role'] not in ('FAULT','NORMAL'):
            raise ValueError('19组标注凭据存在重复或非法身份')
        if row['target_role']=='FAULT' and row['fault_component'] not in FAULT_LOCATIONS:
            raise ValueError('未被专家映射的故障部件: '+row['fault_component'])
        if row['target_role']=='NORMAL' and (row['fault_component'] or row['fault'] or row['onset_s']):
            raise ValueError('正常运行不得含故障源归属')
        models.add(model);by_token[token]=row
    if len(rows)!=19 or sum(r['target_role']=='FAULT' for r in rows)!=14:
        raise ValueError('标注清单不再是已审查的19组（14故障+5正常）')
    return by_token


def channel_coverage():
    channels=_csv(CHANNEL_CATALOG)
    lookup={}
    for item in channels:
        lookup.setdefault((item['subsystem'],item['component']),[]).append(item['original_csv_field'])
    if len(channels)!=56:
        raise ValueError('遥测清单不再包含56个已核查字段')
    return lookup


def _tables(conn):
    return {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _verified_row(run, doc):
    errors=[]
    checks={
        'token':'token', 'model_alias':'model_alias', 'run_role':'target_role',
        'split':'split', 'group_id':'group', 'fault_type':'fault',
        'fault_component':'fault_component', 'normal_frames':'normal_frames',
        'fault_frames':'fault_frames', 'source_sha256':'source_sha256',
    }
    for field,key in checks.items():
        expected=doc[key]
        observed=run[field]
        if field in ('normal_frames','fault_frames'):
            equal=int(expected)==int(observed)
        else:
            equal=str(observed or '')==expected
        if not equal:errors.append(f'{field}_mismatch')
    expected_time=doc['onset_s']
    observed_time=run['onset_s']
    if expected_time:
        if observed_time is None or not math.isclose(float(expected_time),float(observed_time),rel_tol=0,abs_tol=1e-6):
            errors.append('onset_mismatch')
    elif observed_time is not None:
        errors.append('unexpected_onset')
    if run['annotation_scope']!='simulation_run' or run['metadata_not_model_features']!=1:
        errors.append('invalid_annotation_scope')
    if run['data_role']!='simulation':errors.append('invalid_source_role')
    if run['run_role']=='FAULT' and (run['fault_end_exclusive_s'] is None or
           run['onset_s'] is None or float(run['fault_end_exclusive_s'])<=float(run['onset_s'])):
        errors.append('invalid_fault_end')
    return errors


def _inspect_full_archive(db_path, targets_zip):
    """Compare imported DB runs to the full label archive (not all need import).

    targets.zip contains no raw CSV SHA256 or per-parameter event labels.
    """
    evidence = read_targets(targets_zip)
    verified = check_annotated_database(db_path, evidence)
    path = Path(db_path).resolve(strict=True)
    observable = channel_coverage()
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        rows=[dict(r) for r in conn.execute('SELECT a.*,s.source_name,s.source_sha256 FROM v2_run_annotations a JOIN v2_sources s ON s.id=a.source_id ORDER BY a.source_id')]
        locations={r['source_id']:dict(r) for r in conn.execute('SELECT * FROM v2_run_fault_locations')} if 'v2_run_fault_locations' in _tables(conn) else {}
    issues={r['source_id']:r['errors'] for r in verified['issues']}
    result=[]
    for run in rows:
        item=evidence.get(run['token'])
        errors=list(issues.get(run['source_id'],[]))
        source=item['target']['component'] if item else None
        loc=FAULT_LOCATIONS.get(source) if source else None
        covered=observable.get(loc[:2],[]) if loc else []
        existing=locations.get(run['source_id'])
        if source and not loc:
            errors.append('unmapped_fault_component')
        if existing and loc and (existing['subsystem'], existing['device']) != loc[:2]:
            errors.append('conflicting_stored_fault_location')
        if existing and loc and existing['classification_basis'] != loc[2]:
            errors.append('conflicting_mapping_basis')
        if existing and not loc:
            errors.append('normal_run_has_fault_location')
        status=('blocked' if errors else 'ready_to_install' if loc and not existing else 'verified' if loc else 'verified_normal')
        result.append({'source_id':run['source_id'],'token':run['token'],
            'model_alias':run['model_alias'],'split':run['split'],
            'run_role':run['run_role'],'fault_type':run['fault_type'],
            'fault_component':source,
            'location':{'subsystem':loc[0],'device':loc[1],'basis':loc[2]} if loc else None,
            'direct_measurement_channels':covered,
            'direct_measurement_count':len(covered),
            'parameter_truth_status':'requires_explicit_source_scoped_events' if source else 'verified_normal_run',
            'status':status,'errors':errors})
    return {'schema':'aas-phase46-target-archive-subset-audit-v1',
        'input_database':str(path),'archive_total':len(evidence),
        'database_runs':len(rows),'matched_tokens':verified['matched_archive_tokens'],
        'missing_tokens':[], 'unimported_archive_tokens':verified['archive_unimported_tokens'],
        'verified_faults':sum(r['run_role']=='FAULT' and not r['errors'] for r in result),
        'direct_observable_faults':sum(r['run_role']=='FAULT' and not r['errors'] and r['direct_measurement_count']>0 for r in result),
        'needs_install':sum(r['status']=='ready_to_install' for r in result),
        'blocked':sum(r['status']=='blocked' for r in result),
        'raw_telemetry_hash_verified':False,
        'runs':result,'semantics':{
            'component':'known injected origin only; not parameter-level truth',
            'parameter':'requires reviewed source+segment events',
            'normal':'verified NORMAL run or strictly pre-onset',
            'split':'train normal fit; held-out splits evaluated separately',
            'unknown':'no automatic healthy or abnormal assertion'}}


def inspect(db_path, *, manifest=DEFAULT_MANIFEST, targets_zip=None):
    if targets_zip is not None:
        return _inspect_full_archive(db_path,targets_zip)
    manifest=evidence_manifest(manifest)
    observable=channel_coverage()
    path=Path(db_path).resolve(strict=True)
    if not path.is_file():raise ValueError('数据库路径不是普通文件')
    uri=f'file:{path.as_posix()}?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        tables=_tables(conn)
        if not {'v2_sources','v2_run_annotations','v2_segments','v2_channels','v2_samples'}<=tables:
            raise ValueError('不是带运行标注的 V2 遥测数据库；请先运行原有标注导入流程')
        has_loc='v2_run_fault_locations' in tables
        rows=[dict(x) for x in conn.execute('''SELECT a.*,s.source_sha256,
            (SELECT MIN(g.role) FROM v2_segments g WHERE g.source_id=a.source_id) AS data_role
            FROM v2_run_annotations a JOIN v2_sources s ON s.id=a.source_id ORDER BY a.source_id''')]
        locations={r['source_id']:dict(r) for r in conn.execute('SELECT * FROM v2_run_fault_locations')} if has_loc else {}
        results=[]
        seen=set()
        for run in rows:
            doc=manifest.get(run['token'])
            errors=['token_not_in_manifest'] if doc is None else _verified_row(run,doc)
            if run['token'] in seen:errors.append('duplicate_token')
            seen.add(run['token'])
            if doc is not None:
                source=doc['fault_component']
                location=FAULT_LOCATIONS.get(source) if source else None
                channels=observable.get(location[:2],[]) if location else []
            else:
                source='';location=None;channels=[]
            existing=locations.get(run['source_id'])
            if existing and location:
                if (existing['subsystem'],existing['device']) != location[:2]:
                    errors.append('conflicting_stored_fault_location')
                elif existing['classification_basis']!=location[2]:
                    errors.append('conflicting_mapping_basis')
            if existing and not location:errors.append('normal_run_has_fault_location')
            status=('blocked' if errors else
                    'ready_to_install' if location and not existing else
                    'verified' if location else 'verified_normal')
            results.append({
                'source_id':run['source_id'], 'model_alias':run['model_alias'],
                'token':run['token'], 'split':run['split'],'run_role':run['run_role'],
                'fault_type':run['fault_type'],'fault_component':source,
                'location':{'subsystem':location[0],'device':location[1],'basis':location[2]} if location else None,
                'direct_measurement_channels':channels,
                'direct_measurement_count':len(channels),
                'parameter_truth_status':'requires_explicit_source_scoped_events' if location else 'not_a_fault_run',
                'status':status,'errors':errors,
            })
    return {
        'schema':'aas-phase45-truth-audit-v1','input_database':str(path),
        'manifest_total':19,'database_runs':len(rows),'matched_tokens':sum(r['token'] in manifest for r in rows),
        'missing_tokens':sorted(set(manifest)-seen),
        'verified_faults':sum(r['run_role']=='FAULT' and not r['errors'] for r in results),
        'direct_observable_faults':sum(r['run_role']=='FAULT' and not r['errors'] and r['direct_measurement_count']>0 for r in results),
        'needs_install':sum(r['status']=='ready_to_install' for r in results),
        'blocked':sum(r['status']=='blocked' for r in results),
        'runs':results,
        'semantics':{'component':'verified_injected_fault_source_not_each_channel_fault',
                     'parameter':'never_inferred_from_run_labels_or_statistical_contrast',
                     'normal':'verified_normal_run_only','unknown':'not_automatically_healthy'},
    }


def apply_to_new_copy(source_db, output_db, *, require_complete=True, targets_zip=None):
    source=Path(source_db).resolve(strict=True)
    destination=Path(output_db).resolve()
    if destination.exists():raise FileExistsError('禁止覆盖任何已存在的数据库: '+str(destination))
    if source==destination:raise ValueError('不可覆盖原数据库')
    report=inspect(source,targets_zip=targets_zip)
    if report['blocked'] or (targets_zip is None and require_complete and (report['database_runs']!=19 or report['missing_tokens'])):
        raise ValueError('数据库运行证据不完整或存在冲突：拒绝写入；请先检查 --report')
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        # SQLite backup includes a committed snapshot, unlike copying a live WAL file.
        source_uri=f'file:{source.as_posix()}?mode=ro'
        with closing(sqlite3.connect(source_uri,uri=True)) as reader, \
             closing(sqlite3.connect(destination)) as writer, writer:
            reader.backup(writer)
            writer.execute('PRAGMA foreign_keys=ON')
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('''CREATE TABLE IF NOT EXISTS v2_run_fault_locations(
                source_id INTEGER PRIMARY KEY REFERENCES v2_run_annotations(source_id),
                subsystem TEXT NOT NULL,device TEXT NOT NULL,
                classification_basis TEXT NOT NULL,note TEXT NOT NULL)''')
            for row in report['runs']:
                if row['status']!='ready_to_install':continue
                loc=row['location']
                writer.execute('''INSERT INTO v2_run_fault_locations
                  (source_id,subsystem,device,classification_basis,note) VALUES (?,?,?,?,?)''',
                  (row['source_id'],loc['subsystem'],loc['device'],loc['basis'],
                   'Phase45: run source identity and sha256 checked against bundled 19-run manifest; '
                   'injection origin ONLY, not parameter-level ground truth.'))
            if writer.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('目标数据库存在外键错误')
            writer.commit()
        check=inspect(destination,targets_zip=targets_zip)
        if check['blocked'] or check['needs_install']:
            raise ValueError('新数据库复核未通过')
        return check
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description='审查19组卫星故障源真值；默认严格只读')
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--report',type=Path,help='输出JSON审查结果（不修改数据库）')
    parser.add_argument('--apply-copy',type=Path,help='创建一份新的已补齐映射的数据库；原文件保持不变')
    parser.add_argument('--allow-partial',action='store_true',help='仅针对旧19组清单的部分运行生成新数据库')
    parser.add_argument('--targets-zip',type=Path,help='完整targets.zip：核验所有已导入来源，不要求导入全部3548组')
    args=parser.parse_args()
    result=apply_to_new_copy(args.database,args.apply_copy,require_complete=not args.allow_partial,targets_zip=args.targets_zip) if args.apply_copy else inspect(args.database,targets_zip=args.targets_zip)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(output,encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
