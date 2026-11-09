"""Verified targets.zip metadata contract for run-scoped V2 truth.

The archive describes *run* fault injection sources, not per-channel failure.
No field from this module may be used as a detector input feature. This reader
never extracts archive members to disk or modifies telemetry databases.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

REQUIRED = frozenset({'token','split','group','role','fault','onset_s','component','metadata_not_model_features'})
SPLITS = frozenset({'train','validation','test_confirmation','ood_scene','ood_joint','ood_severity'})
HELDOUT_SPLITS = frozenset(SPLITS - {'train'})
TOKEN_RE = re.compile(r'[0-9a-f]{24}\Z')
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_MEMBER_BYTES = 16 * 1024


def read_targets(path: str | Path):
    """Return per-token evidence with verified member SHA256, fail closed."""
    records = {}
    group_split = {}
    uncompressed = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError('标签文件条目过多')
        for info in infos:
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            if (len(name.parts) != 2 or name.parts[0] != 'targets' or
                    name.suffix != '.json' or not TOKEN_RE.fullmatch(name.stem)):
                raise ValueError('标签 ZIP 只允许 targets/<24位token>.json')
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError('单份标签超出安全大小限制')
            uncompressed += info.file_size
            if uncompressed > MAX_ARCHIVE_BYTES:
                raise ValueError('标签 ZIP 总解压大小超出限制')
            token = name.stem
            if token in records:
                raise ValueError('重复 token: '+token)
            raw = archive.read(info)
            try:
                item = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError('标签 JSON 非法: '+token) from exc
            if not isinstance(item,dict) or set(item)!=REQUIRED or item.get('token')!=token:
                raise ValueError('标签字段或 token 不匹配: '+token)
            if (item['split'] not in SPLITS or
                    not isinstance(item['group'],str) or not TOKEN_RE.fullmatch(item['group']) or
                    item['role'] not in ('NORMAL','FAULT') or
                    item['metadata_not_model_features'] is not True):
                raise ValueError('划分、分组、用途或防泄漏声明无效: '+token)
            if item['role']=='NORMAL':
                if any(item[k] is not None for k in ('fault','onset_s','component')):
                    raise ValueError('NORMAL 运行不能携带故障源: '+token)
            else:
                if any(not isinstance(item[k],str) or not item[k] or len(item[k])>120
                       for k in ('fault','component')):
                    raise ValueError('FAULT 缺少故障类型/源部件: '+token)
                onset=item['onset_s']
                if isinstance(onset,bool) or not isinstance(onset,(int,float)) or not math.isfinite(onset) or onset<0:
                    raise ValueError('FAULT 起始时刻非法: '+token)
            previous = group_split.setdefault(item['group'],item['split'])
            if previous != item['split']:
                raise ValueError('同一 group 横跨数据划分，存在泄漏: '+item['group'])
            records[token] = {'target':item, 'target_sha256':hashlib.sha256(raw).hexdigest()}
    if not records:
        raise ValueError('标签 ZIP 未包含有效标签')
    return records


def summarize(records):
    rows = [v['target'] for v in records.values()]
    counts = Counter((r['split'],r['role']) for r in rows)
    components = Counter(r['component'] for r in rows if r['role']=='FAULT')
    faults = Counter(r['fault'] for r in rows if r['role']=='FAULT')
    return {'schema':'aas-target-archive-v1', 'total':len(rows),
            'normal':sum(r['role']=='NORMAL' for r in rows),
            'fault':sum(r['role']=='FAULT' for r in rows),
            'group_count':len({r['group'] for r in rows}),
            'fault_type_count':len(faults), 'component_count':len(components),
            'splits':{split:{role:counts[split,role] for role in ('NORMAL','FAULT')}
                      for split in sorted(SPLITS)},
            'fault_types':dict(sorted(faults.items())),
            'source_components':dict(sorted(components.items())),
            'labels_are':'run_level_ground_truth',
            'model_features_must_exclude':['token','split','group','role','fault','onset_s','component'],
            'target_split_policy':{'train':'NORMAL-only fitting; FAULT train runs are not normal fitting data',
               'validation':'held-out evaluation', 'test_confirmation':'held-out confirmation',
               'ood_scene':'separate out-of-distribution evaluation',
               'ood_joint':'separate out-of-distribution evaluation',
               'ood_severity':'separate out-of-distribution evaluation'}}


def check_annotated_database(database: str | Path, records):
    """Compare selected database runs against the actual archive member hashes.

    A targets ZIP has no raw telemetry SHA, no machine-readable per-channel events,
    and no synthetic satellite model alias; these cannot be verified here.
    """
    import sqlite3
    path = Path(database).resolve(strict=True)
    with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'v2_sources','v2_run_annotations'}<=tables:
            raise ValueError('该数据库缺少 v2_run_annotations / v2_sources')
        rows=[dict(r) for r in conn.execute('''SELECT a.*,s.source_name,s.source_sha256
              FROM v2_run_annotations a JOIN v2_sources s ON s.id=a.source_id''')]
        total=conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]
        if len(rows)!=total:
            raise ValueError('部分遥测来源没有对应的运行标签，拒绝认为全库已核验')
    issues=[]; matched=0; selected=set()
    for row in rows:
        token=row['token']; selected.add(token)
        evidence=records.get(token)
        errors=[]
        if evidence is None:
            errors.append('token_not_in_uploaded_archive')
        else:
            target=evidence['target'];matched+=1
            expected = {'split':target['split'],'group_id':target['group'],
                'run_role':target['role'],'fault_type':target['fault'],
                'fault_component':target['component']}
            for field,value in expected.items():
                if row[field] != value:
                    errors.append(field+'_mismatch')
            onset=target['onset_s']
            if (onset is None) != (row['onset_s'] is None) or (onset is not None and
                (row['onset_s'] is None or not math.isclose(float(onset),float(row['onset_s']),rel_tol=0,abs_tol=1e-6))):
                errors.append('onset_mismatch')
            if row['target_sha256']!=evidence['target_sha256']:
                errors.append('target_sha256_mismatch')
        if row['source_name'] != token+'.csv.gz':
            errors.append('source_filename_token_mismatch')
        if row['annotation_scope']!='simulation_run' or row['metadata_not_model_features']!=1:
            errors.append('invalid_annotation_scope')
        if errors:
            issues.append({'token':token,'source_id':row['source_id'],'errors':errors})
    # Only selected runs are required to be in a subset DB. Unselected archive
    # members are never assigned made-up aliases or interpreted as imported.
    return {'database_runs':len(rows),'matched_archive_tokens':matched,
            'archive_unimported_tokens':len(set(records)-selected),
            'blocked':len(issues), 'issues':issues,
            'raw_telemetry_hash_verified':False,
            'model_alias_verified_from_archive':False,
            'scope':'selected_database_runs_only',
            'warning':'targets.zip 不提供原始 CSV 的 SHA256；需另用原始数据或导入清单核验。'}
