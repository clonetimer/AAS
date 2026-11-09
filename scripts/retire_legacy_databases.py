"""Retire migrated *legacy database files* from active datasets.

Default: non-destructive audit. With --apply: verify installed V2 integrity and
migration-source metadata; copy every source to an isolated backup and verify
hashes; only then remove the legacy source files. Does not touch V2 samples,
quarantine, model metadata, or SQLite system tables.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

LEGACY=('history_sample.db','simulation.db','history_manifest.db','historydb.db')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def retire(root:Path, *, apply:bool=False):
    root=Path(root).resolve(); datasets=root/'datasets'; v2=datasets/'aas_telemetry_v2.sqlite'
    if not v2.is_file():raise ValueError('V2 数据库未安装，不能退出旧数据源')
    if any((datasets/(v2.name+s)).exists() for s in ('-wal','-journal')):
        raise ValueError('V2 存在未检查的事务日志；请停止程序并完成 checkpoint 后重试')
    with sqlite3.connect(v2.as_uri()+'?mode=ro',uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('V2 完整性或外键校验不通过')
        if c.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()[0] != '2':
            raise ValueError('V2 schema_version 不符合预期')
        sources={r[0]:r[1] for r in c.execute('SELECT source_name,source_row_count FROM v2_sources')}
        # Migration evidence must account for every source row, including
        # quarantined legacy rows. Registering a source name alone is not enough.
        coverage={row[0]:row[1] for row in c.execute('''
            SELECT src.source_name,
              (SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g
                 ON g.id=s.segment_id WHERE g.source_id=src.id) +
              (SELECT COUNT(*) FROM v2_legacy_rows l JOIN v2_segments g
                 ON g.id=l.segment_id WHERE g.source_id=src.id)
            FROM v2_sources src''')}
        for source_name, declared_rows in sources.items():
            if coverage.get(source_name) != declared_rows:
                raise ValueError(f'{source_name} 的迁移记录数核对不一致；拒绝退出旧数据库')
        if c.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]<1:
            raise ValueError('V2 没有遥测样本，禁止退出历史数据源')
    original=[datasets/name for name in LEGACY if (datasets/name).is_file()]
    for item in original:
        if item.name not in sources or sources[item.name]<=0:
            raise ValueError(f'{item.name} 尚无非空的 V2 迁移来源登记，不允许退出')
        if any((datasets/(item.name+s)).exists() for s in ('-wal','-journal')):
            raise ValueError(f'{item.name} 存在活动事务日志，请先停止服务并 checkpoint')
    manifest={'v2':str(v2),'legacy':[{'name':p.name,'bytes':p.stat().st_size,
        'sha256':digest(p),'v2_source_rows':sources[p.name]} for p in original],
        'timestamp_utc':datetime.now(timezone.utc).isoformat(),'mode':'apply' if apply else 'dry-run'}
    if not apply or not original:return manifest
    # A new backup directory avoids collisions with previous retirement attempts.
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    backup_dir=root/'backups'/f'legacy-retired-{stamp}'
    backup_dir.mkdir(parents=True,exist_ok=False)
    for item in original:
        dest=backup_dir/item.name
        shutil.copy2(item,dest)
        if digest(dest)!=next(v['sha256'] for v in manifest['legacy'] if v['name']==item.name):
            raise RuntimeError('旧数据库备份校验失败；原始文件未删除')
    (backup_dir/'retirement_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    for item in original:item.unlink()
    manifest['backups']=str(backup_dir)
    return manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--apply',action='store_true',help='执行：备份核验后从活动 datasets 移除旧库')
    args=ap.parse_args()
    print(json.dumps(retire(args.root,apply=args.apply),indent=2,ensure_ascii=False))


if __name__=='__main__':main()
