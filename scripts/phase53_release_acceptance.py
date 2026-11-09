"""Phase 53 fail-closed release-evidence check for an *offline* V2 migration.

This tool does not change either SQLite file and does not claim that a Linux
smoke test constitutes Windows or production sign-off. It consumes a complete
fixed gate report, reruns full sample/metadata verification, and compares the
same catalog and bounded chart API used in the application.

Usage: py -m scripts.phase53_release_acceptance --source old.sqlite \
    --output compact.sqlite --gate-report outputs/gate.json --report outputs/release.json
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3

from scripts.verify_v2_compaction import verify
from scripts.server.telemetry_v2_access import list_channels, query_charts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'tests'/'v2_stable_gate.json'
ROLES = ('train','test','simulation','raw','unspecified','processed','simulated_fault')


def check_gate(report_file: Path, manifest_file: Path = MANIFEST):
    report_file=Path(report_file)
    if not report_file.is_file():
        raise FileNotFoundError('缺少完整门禁报告；拒绝以局部测试结果代替')
    gate=json.loads(report_file.read_text(encoding='utf-8'))
    manifest=json.loads(Path(manifest_file).read_text(encoding='utf-8'))
    expected=manifest['stable_nodes']
    batches=gate.get('batches',[])
    nodes=[node for batch in batches for node in batch.get('nodes',[])]
    if (gate.get('status')!='passed' or not batches
        or gate.get('stable_node_count')!=len(expected)
        or gate.get('external_node_count') not in (0,None)
        or nodes!=expected
        or any(batch.get('status')!='passed' or batch.get('exit_code')!=0 for batch in batches)):
        raise ValueError('固定门禁证据不完整、节点不一致或批次未正常退出')
    # Gate runner validates exact UI assets at runtime; verify they have not
    # drifted between execution and generating the acceptance report.
    for name,digest in manifest['frontend_sha256'].items():
        file=ROOT/name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'前端资源在门禁后发生变化：{name}')
    return {'status':'verified','nodes':len(nodes),'batch_count':len(batches),
            'report_sha256':hashlib.sha256(report_file.read_bytes()).hexdigest()}


def business_smoke(source: Path, output: Path, *, max_channels=3):
    """Exercise the real read-only channel listing and chart queries.

    This bounded smoke is supplementary to verify(), which compares *all*
    samples and metadata. It does not falsely imply a full model-training run.
    """
    outcomes=[]
    for role in ROLES:
        first=list_channels(source,role=role,limit=None)
        second=list_channels(output,role=role,limit=None)
        if first!=second:
            raise ValueError(f'业务目录查询迁移前后不一致：{role}')
        ids=[entry['id'] for entry in first['items']][:max_channels]
        checked=[]
        for channel_id in ids:
            # Per-channel avoids introducing unrelated same-clock assumptions.
            try:
                before=query_charts(source,role=role,channel_ids=[channel_id],limit=100)
            except ValueError as exc:
                try:
                    query_charts(output,role=role,channel_ids=[channel_id],limit=100)
                except ValueError as second_exc:
                    if str(exc)!=str(second_exc):
                        raise ValueError(f'图表查询失败信息变更：{role}/{channel_id}') from second_exc
                    checked.append({'id':channel_id,'outcome':'same_rejection'})
                    continue
                raise ValueError(f'新库错误接受了旧库拒绝的查询：{role}/{channel_id}') from exc
            after=query_charts(output,role=role,channel_ids=[channel_id],limit=100)
            if before!=after:
                raise ValueError(f'业务图表查询迁移前后不一致：{role}/{channel_id}')
            checked.append({'id':channel_id,'outcome':'matched',
                            'points':sum(len(s['points']) for s in before['series'])})
        outcomes.append({'role':role,'channel_count':first['count'],'queries':checked})
    return {'status':'verified','roles':outcomes,
            'scope':'bounded catalog/chart regression, not an end-to-end training or Windows test'}


def audit(source: Path, output: Path, gate_report: Path, *, production=False):
    source=Path(source).resolve()
    output=Path(output).resolve()
    if source==output:
        raise ValueError('源库和迁移副本必须不同')
    gate=check_gate(gate_report)
    # A persistent connection's data_version detects commits by *other*
    # connections across the whole release audit, including its business smoke.
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as old_watch, \
         closing(sqlite3.connect(output.as_uri()+'?mode=ro',uri=True)) as new_watch:
        start=(old_watch.execute('PRAGMA data_version').fetchone()[0],
               new_watch.execute('PRAGMA data_version').fetchone()[0])
        full=verify(source,output)
        smoke=business_smoke(source,output)
        end=(old_watch.execute('PRAGMA data_version').fetchone()[0],
             new_watch.execute('PRAGMA data_version').fetchone()[0])
        if start != end:
            raise ValueError('发布验收期间发生并发数据库写入；停止服务后重新核验')
    blockers=[]
    if not production:
        blockers.append('本轮使用隔离测试库，尚未核验用户正式业务数据库')
    if os.name!='nt':
        blockers.append('当前不是 Windows 原生运行与验收环境')
    # It would be misleading to mark Stable without actual user acceptance,
    # even when someone sets --production on a Windows workstation.
    blockers.append('尚未完成正式业务库切换、回滚演练及用户签收')
    return {'phase':53,'status':'candidate_verified','generated_utc':datetime.now(timezone.utc).isoformat(),
            'platform':platform.platform(),'production_data_claimed':bool(production),
            'source':str(source),'output':str(output),
            'full_compaction_verification':full,'application_business_smoke':smoke,
            'fixed_v2_gate':gate,'stable_promotion_blockers':blockers,
            'release_decision':'Candidate (not Stable)'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--gate-report',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--production',action='store_true',help='Explicitly record that the input is the real offline business database; not proof of acceptance')
    args=p.parse_args(argv)
    if args.report.exists():p.error('报告目标已存在，请另选路径（不会覆盖验收证据）')
    result=audit(args.source,args.output,args.gate_report,production=args.production)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive create prevents accidental overwrite, even under concurrent invocations.
    with args.report.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
        f.flush();os.fsync(f.fileno())
    print(json.dumps({'status':result['status'],'release_decision':result['release_decision'],
        'sample_rows':result['full_compaction_verification']['sample_rows'],
        'blockers':result['stable_promotion_blockers'],'report':str(args.report)},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
