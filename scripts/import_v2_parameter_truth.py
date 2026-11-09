"""Install manually confirmed, source-scoped parameter event labels in a NEW DB.

Never synthesizes point labels from global run fault intervals or statistics.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3

from scripts.audit_v2_fault_truth import apply_to_new_copy, inspect
from scripts.server.v2_source_parameter_events import install_reviewed_events


def apply_events_copy(db_path, output_path, event_file, *, require_complete=True, targets_zip=None):
    source=Path(db_path).resolve(strict=True)
    target=Path(output_path).resolve()
    if target.exists():raise FileExistsError('拒绝覆盖已有数据库')
    payload=json.loads(Path(event_file).read_text('utf-8'))
    if not isinstance(payload,dict) or set(payload)!={'events'}:
        raise ValueError('评审文件必须仅包含 events 数组')
    # install audited run-component map on the same new copy first, then
    # explicitly reviewed point events; never write to the source database.
    apply_to_new_copy(source,target,require_complete=require_complete,targets_zip=targets_zip)
    try:
        # NB: sqlite3.connect() used as a context manager only commits/rolls back;
        # it does NOT close the handle. Close explicitly before any unlink so the
        # cleanup path below can delete the file on Windows (open files are locked).
        conn=sqlite3.connect(target)
        try:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            # executescript() inside installer creates the table, then inserts.
            events=install_reviewed_events(conn,payload['events'])
            if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('事件映射导致外键错误')
            conn.commit()
        finally:
            conn.close()
        return {'database':str(target),'installed_events':len(events),
                'run_mapping_audit':{'blocked':inspect(target,targets_zip=targets_zip)['blocked']},'events':events}
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main():
    p=argparse.ArgumentParser(description='仅在新数据库副本中导入人工复核的参量区间真值')
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--events',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-partial',action='store_true')
    p.add_argument('--targets-zip',type=Path,help='按完整标签成员SHA核验，而非旧19组固定清单')
    args=p.parse_args()
    print(json.dumps(apply_events_copy(args.database,args.output,args.events,
          require_complete=not args.allow_partial,targets_zip=args.targets_zip),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
