"""Install the prebuilt Phase 7 V2 database from a zstd archive, atomically.

Refuses to overwrite an existing database. Requires `pip install zstandard`.
Verifies SHA256, V2 schema, SQLite integrity, and exact sample counts before
publishing the new database file to the app. No legacy data is imported.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3

HERE=Path(__file__).resolve().parents[1]
MANIFEST=HERE/'docs/PHASE7_DATA_MANIFEST.json'
DEFAULT_DB=HERE/'datasets/aas_telemetry_v2.sqlite'


def filehash(path):
    h=hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda:src.read(4*1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def verify(db):
    data=json.loads(MANIFEST.read_text(encoding='utf8'))
    with sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True) as conn:
        if conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()[0]!='2':
            raise ValueError('数据库不是 V2')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('数据库完整性检查失败')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('数据库存在外键错误')
        for table,key in [('v2_samples','samples'),('v2_channels','channels'),('v2_legacy_rows','legacy_rows'),('v2_fault_events','fault_events')]:
            if conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]!=data[key]:
                raise ValueError(f'{table} 记录数量与数据清单不匹配')
        for table,key in [('v2_run_annotations','run_annotations'),('v2_run_fault_locations','run_fault_locations')]:
            if key in data and conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]!=data[key]:
                raise ValueError(f'{table} 标注记录数量与交付清单不符')
        if data.get('label_0') is not None:
            counts=dict(conn.execute('SELECT label_int,COUNT(*) FROM v2_samples GROUP BY label_int'))
            if counts!={0:data['label_0'],1:data['label_1']}:
                raise ValueError('数据库标签分布与标注清单不一致')
        if data.get('targets_zip_sha256') is not None:
            bound=conn.execute("SELECT value FROM v2_meta WHERE key='target_annotation_sha256'").fetchone()
            if not bound or bound[0]!=data['targets_zip_sha256']:
                raise ValueError('标签来源文件 SHA256 不一致')
    expected=data.get('database_sha256')
    if expected and filehash(db)!=expected:
        raise ValueError('数据库 SHA256 校验失败')
    return data


def install(archive,dest):
    import zstandard
    archive=Path(archive).resolve();dest=Path(dest).resolve()
    if not archive.is_file(): raise FileNotFoundError(f'找不到数据库压缩文件: {archive}')
    if dest.exists(): raise FileExistsError(f'目标数据库已存在，拒绝覆盖: {dest}')
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix(dest.suffix+'.installing')
    if temp.exists():raise FileExistsError(f'临时文件已存在，请先排查: {temp}')
    try:
        with archive.open('rb') as src,temp.open('xb') as out:
            decoder=zstandard.ZstdDecompressor()
            decoder.copy_stream(src,out)
            out.flush();os.fsync(out.fileno())
        data=verify(temp)
        if dest.exists():raise FileExistsError('验证期间目标被创建，拒绝覆盖')
        os.replace(temp,dest)
        return data
    finally:
        temp.unlink(missing_ok=True)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--archive',type=Path,required=True,help='数据库 .sqlite.zst 文件')
    ap.add_argument('--destination',type=Path,default=DEFAULT_DB)
    args=ap.parse_args()
    result=install(args.archive,args.destination)
    print(f"安装并验证成功：{args.destination}；{result['samples']} 条采样，{result['channels']} 个参量")

if __name__=='__main__':main()
