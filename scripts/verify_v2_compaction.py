"""Lossless Phase 52 acceptance check for an offline V2 compaction copy.

Compares sample identity, numerical telemetry, timestamp/clock, original labels,
quality metadata, and the fully resolved raw JSON on EVERY sample; also verifies
all unchanged tables by primary-key-ordered content hashes. No database writes.

Example: py -m scripts.verify_v2_compaction --source datasets/old.sqlite \
    --output datasets/old_compact.sqlite --report outputs/compaction_verification.json

Run only against a quiescent source (no writers) for the final deployment audit.
"""
from __future__ import annotations

import argparse
import hashlib
from itertools import zip_longest
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from scripts.server.telemetry_v2_raw_rows import has_raw_store, raw_store_health


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def _schema(conn):
    return {name:typ for typ,name in conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")}


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info({_quote(table)})')]


def _ordered_query(conn, table, *, samples=False):
    columns = _columns(conn, table)
    if samples:
        other = [c for c in columns if c != 'raw_row_json']
        fields = [f's.{_quote(c)}' for c in other]
        if has_raw_store(conn):
            raw = "CASE WHEN s.raw_row_json='' THEN r.raw_row_json ELSE s.raw_row_json END"
            joins = ''' JOIN v2_segments g ON g.id=s.segment_id
                LEFT JOIN v2_source_raw_rows r ON r.source_id=g.source_id
                    AND r.source_ordinal=s.source_ordinal'''
        else:
            raw = 's.raw_row_json'
            joins = ''
        return f'SELECT {", ".join(fields)}, {raw} FROM v2_samples s {joins} ORDER BY s.id'
    info=list(conn.execute(f'PRAGMA table_info({_quote(table)})'))
    pk = [row[1] for row in sorted(info, key=lambda r:r[5]) if row[5]]
    if pk:
        order = ','.join(_quote(col) for col in pk)
    else:
        # Tables without a declared PK are compared as a multiset, not rowid.
        order = ','.join(str(i+1) for i in range(len(columns)))
    return f'SELECT * FROM {_quote(table)} ORDER BY {order}'


def _encode(row):
    def stable(value):
        if isinstance(value, bytes):
            return {'blob_hex':value.hex()}
        return value
    return (json.dumps([stable(v) for v in row],ensure_ascii=False,
                       separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def _compare_rows(source, output, sql_source, sql_output, table):
    left=source.execute(sql_source)
    right=output.execute(sql_output)
    digest=hashlib.sha256()
    count=0
    for index,(a,b) in enumerate(zip_longest(left,right),1):
        if a is None or b is None:
            raise ValueError(f'{table} 第 {index} 行：新旧记录数量不一致')
        if a!=b:
            # Do not include telemetry or labels in exception messages.
            raise ValueError(f'{table} 第 {index} 行：迁移前后内容不同')
        if table=='v2_samples' and a[-1] is None:
            raise ValueError(f'{table} 第 {index} 行：原始行引用无法还原')
        digest.update(_encode(a))
        count=index
    return {'rows':count,'sha256':digest.hexdigest()}


def verify(source, output):
    """Fail-closed detailed equality report, independent of file allocation size."""
    source=Path(source).resolve();output=Path(output).resolve()
    if source==output:raise ValueError('必须使用不同的新旧数据库路径')
    with closing(_connect(source)) as old, closing(_connect(output)) as new:
        # Hold two consistent read snapshots throughout the table-by-table
        # comparison. Catch committed concurrent writes rather than silently
        # auditing different database versions in successive SELECT statements.
        revisions = (old.execute('PRAGMA data_version').fetchone()[0],
                     new.execute('PRAGMA data_version').fetchone()[0])
        old.execute('BEGIN')
        new.execute('BEGIN')
        for tag,db in (('原库',old),('新库',new)):
            if db.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone() != ('2',):
                raise ValueError(f'{tag} 不是有效 V2 数据库')
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError(f'{tag} quick_check 失败')
            fk=list(db.execute('PRAGMA foreign_key_check'))
            if fk:raise ValueError(f'{tag} 外键检查失败')
            health=raw_store_health(db)
            if health['dangling_refs']:
                raise ValueError(f'{tag} 存在 {health["dangling_refs"]} 个悬空原始行引用')
        old_schema,new_schema=_schema(old),_schema(new)
        # Compaction is allowed to add only the shared raw-row table/view.
        if set(old_schema)-set(new_schema):
            raise ValueError('压缩结果缺失原库的表或视图')
        if set(new_schema)-set(old_schema)-{'v2_source_raw_rows','v2_sample_raw_resolved'}:
            raise ValueError('压缩结果包含未经批准的新表或视图')
        old_columns=_columns(old,'v2_samples')
        if old_columns!=_columns(new,'v2_samples'):
            raise ValueError('v2_samples 字段发生不兼容变化')
        samples=_compare_rows(old,new,_ordered_query(old,'v2_samples',samples=True),
                              _ordered_query(new,'v2_samples',samples=True),'v2_samples')
        tables={name for name,typ in old_schema.items() if typ=='table'}-{'v2_samples','v2_source_raw_rows'}
        others={}
        for name in sorted(tables):
            if _columns(old,name)!=_columns(new,name):
                raise ValueError(f'{name} 字段发生不兼容变化')
            others[name]=_compare_rows(old,new,_ordered_query(old,name),_ordered_query(new,name),name)
        result = {'status':'verified','source':str(source),'output':str(output),
                  'sample_rows':samples['rows'],'sample_content_sha256':samples['sha256'],
                  'unchanged_tables':others,'new_raw_storage':raw_store_health(new),
                  'source_bytes':source.stat().st_size,'output_bytes':output.stat().st_size}
        old.execute('ROLLBACK')
        new.execute('ROLLBACK')
        latest = (old.execute('PRAGMA data_version').fetchone()[0],
                  new.execute('PRAGMA data_version').fetchone()[0])
        if revisions != latest:
            raise ValueError('核验期间数据库发生并发写入，结果无效；请停止服务后重试')
        return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--report',type=Path)
    args=p.parse_args(argv)
    result=verify(args.source,args.output)
    if args.report:
        if args.report.exists():
            p.error('报告已存在，拒绝覆盖')
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
