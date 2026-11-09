"""Inspect / query rebuilt AAS V2 SQLite database without writing to it.

Examples:
  python scripts/query_telemetry_v2.py status --db datasets/aas_telemetry_v2.sqlite
  python scripts/query_telemetry_v2.py channels --db datasets/aas_telemetry_v2.sqlite
  python scripts/query_telemetry_v2.py query --db datasets/aas_telemetry_v2.sqlite --namespace history_sample --model GF1 --subsystem subsys1 --device battery --variable capacity --role train
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from rebuild_telemetry_v2 import query_channel  # noqa: E402


def open_ro(file):
    return sqlite3.connect(f'{Path(file).resolve().as_uri()}?mode=ro',uri=True)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['status','channels','query'])
    ap.add_argument('--db',required=True,type=Path)
    ap.add_argument('--namespace')
    for name in ('model','subsystem','device','variable'):
        ap.add_argument('--'+name)
    ap.add_argument('--role',action='append',choices=['train','test','unspecified','simulation','processed','simulated_fault'])
    ap.add_argument('--start')
    ap.add_argument('--end')
    ap.add_argument('--limit',type=int,default=1000)
    ap.add_argument('--include-inferred-role',action='store_true')
    ap.add_argument('--csv',type=Path,help='保存查询结果为 CSV（不覆盖已有文件）')
    args=ap.parse_args(argv)
    if args.command=='query':
        if not all(getattr(args,k) for k in ('namespace','model','subsystem','device','variable')):
            ap.error('query 必须提供 namespace/model/subsystem/device/variable')
        if not args.role:
            ap.error('query 必须指定至少一个 --role（严格限制训练/测试用途）')
        result=query_channel(args.db,args.namespace,args.model,args.subsystem,args.device,args.variable,
                             start=args.start,end=args.end,roles=args.role,limit=args.limit,
                             allow_inferred_roles=args.include_inferred_role)
        if args.csv:
            if args.csv.exists():
                ap.error('拒绝覆盖已有 CSV')
            with args.csv.open('x',encoding='utf-8-sig',newline='') as handle:
                cols=['source_table','role','source_ordinal','timestamp_raw','value_num','label_int','value_raw_json']
                writer=csv.DictWriter(handle,fieldnames=cols)
                writer.writeheader()
                writer.writerows(result)
            print(json.dumps({'rows':len(result),'csv':str(args.csv)},ensure_ascii=False))
        else:
            print(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    with open_ro(args.db) as conn:
        conn.row_factory=sqlite3.Row
        if args.command=='status':
            result={
                'source_dbs':[dict(x) for x in conn.execute('SELECT namespace,source_name,source_row_count FROM v2_sources ORDER BY namespace')],
                'channels':conn.execute('SELECT COUNT(*) FROM v2_channels').fetchone()[0],
                'segments':conn.execute('SELECT COUNT(*) FROM v2_segments').fetchone()[0],
                'canonical_samples':conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0],
                'legacy_rows':conn.execute('SELECT COUNT(*) FROM v2_legacy_rows').fetchone()[0],
                'issues':[dict(x) for x in conn.execute('SELECT issue_code,COUNT(*) AS n FROM v2_issues GROUP BY issue_code')],
            }
        else:
            sql='''SELECT c.namespace,c.satellite_instance,c.model,c.subsystem,c.device,c.variable,
                    COUNT(g.id) AS segments, COALESCE(SUM(g.source_row_count),0) AS physical_rows,
                    GROUP_CONCAT(DISTINCT g.time_domain) AS time_domains
                    FROM v2_channels c LEFT JOIN v2_segments g ON g.channel_id=c.id
                    WHERE 1=1'''
            where=[];params=[]
            for attr in ('namespace','model','subsystem','device','variable'):
                if getattr(args,attr):
                    where.append('c.'+attr+'=?');params.append(getattr(args,attr))
            if where:sql+=' AND '+' AND '.join(where)
            sql+=' GROUP BY c.id ORDER BY c.namespace,c.model,c.subsystem,c.device,c.variable'
            result=[dict(x) for x in conn.execute(sql,params)]
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    main()
