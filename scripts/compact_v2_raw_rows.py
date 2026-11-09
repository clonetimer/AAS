"""Non-destructive Phase51 V2 raw-row compaction.

Creates a separate SQLite database. Original is never modified. Wide sources
are compacted only if every channel sample at each source ordinal has the
EXACT same inline raw JSON. Inconsistent or mixed representations abort rather
than guessing. Raw rows can be read from either representation via
telemetry_v2_raw_rows.resolved_raw_row.

Windows: py -m scripts.compact_v2_raw_rows --source datasets/old.sqlite --output datasets/compacted.sqlite
Use --dry-run to inspect candidates without writing. Never overwrite output.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
from time import perf_counter

from scripts.server.telemetry_v2_raw_rows import ensure_raw_store, raw_store_health
from scripts.verify_v2_compaction import verify


def _candidates(conn):
    sql = '''SELECT g.source_id,COUNT(DISTINCT g.id) AS segments,COUNT(s.id) AS samples,
       MIN(g.role_provenance) AS provenance,MAX(g.role_provenance) AS provenance_last
       FROM v2_segments g JOIN v2_samples s ON s.segment_id=g.id
       GROUP BY g.source_id HAVING COUNT(DISTINCT g.id)>1 ORDER BY g.source_id'''
    return [dict(zip(('source_id','segments','samples','provenance','provenance_last'),r)) for r in conn.execute(sql)]


def _compact_source(db, source_id):
    """Single transaction per source, with a lossless proof before rewriting."""
    segments = [r[0] for r in db.execute('SELECT id FROM v2_segments WHERE source_id=? ORDER BY id',(source_id,))]
    if len(segments)<2:
        return None
    # Do not merge independent physical tables from the legacy rebuild, whose
    # source ordinals may happen to coincide but mean different measurements.
    provenances={r[0] for r in db.execute('SELECT DISTINCT role_provenance FROM v2_segments WHERE source_id=?',(source_id,))}
    if provenances != {'user_explicit'}:
        return None
    total=db.execute('''SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        WHERE g.source_id=?''',(source_id,)).fetchone()[0]
    already=db.execute('''SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        WHERE g.source_id=? AND s.raw_row_json='' ''',(source_id,)).fetchone()[0]
    if already == total:
        return None
    if already:
        raise ValueError(f'来源 {source_id} 存在混合 raw_row_json 状态，请人工核验')
    seed=segments[0]
    distinct=db.execute('SELECT COUNT(*) FROM v2_samples WHERE segment_id=?',(seed,)).fetchone()[0]
    if distinct==0:return None
    # Require exact coverage of all source ordinals, and identical text. The
    # inequality catches both missing ordinals and modified column-specific raw.
    broken=db.execute('''SELECT s.id FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        LEFT JOIN v2_samples b ON b.segment_id=? AND b.source_ordinal=s.source_ordinal
        WHERE g.source_id=? AND (b.id IS NULL OR s.raw_row_json IS NOT b.raw_row_json)
        LIMIT 1''',(seed,source_id)).fetchone()
    if broken:
        raise ValueError(f'来源 {source_id} 存在不一致的原始行，拒绝压缩')
    db.execute('''INSERT INTO v2_source_raw_rows(source_id,source_ordinal,raw_row_json)
        SELECT ?,source_ordinal,raw_row_json FROM v2_samples WHERE segment_id=?''',(source_id,seed))
    changed=db.execute('''UPDATE v2_samples SET raw_row_json='' WHERE segment_id IN
        (SELECT id FROM v2_segments WHERE source_id=?)''',(source_id,)).rowcount
    if changed!=total:
        raise ValueError(f'来源 {source_id} 更新行数不一致，操作回滚')
    return {'source_id':source_id,'shared_rows':distinct,'sample_references':changed}


def _publish_verified_copy(partial: Path, output: Path) -> None:
    """Publish without replacing an existing destination on either platform.

    Windows MoveFile/``os.rename`` refuses to replace an existing target, and
    it moves the fully closed temporary database without a second unlink.
    POSIX ``os.link`` preserves no-clobber semantics (plain os.rename would
    silently replace an existing target there).
    """
    if os.name == 'nt':
        os.rename(partial, output)
    else:
        os.link(partial, output)
        partial.unlink()


def compact(source, output, *, dry_run=False):
    source=Path(source).resolve();output=Path(output).resolve()
    if not source.is_file():raise FileNotFoundError(source)
    if source==output:raise ValueError('输出不得覆盖原始数据库')
    if output.is_symlink():raise FileExistsError('目标路径不能是已有符号链接')
    if output.exists() or Path(str(output)+'.partial').exists():
        raise FileExistsError('目标数据库或中间文件已存在，拒绝覆盖')
    with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True)) as old:
        ver=old.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
        if ver is None or ver[0]!='2':raise ValueError('仅支持 SQLite V2')
        candidates=_candidates(old)
        if dry_run:return {'status':'preview','candidates':candidates,'source_bytes':source.stat().st_size}
        output.parent.mkdir(parents=True,exist_ok=True)
        # Copy + UPDATE + VACUUM can temporarily occupy multiple copies of
        # the old database. Reject before touching the destination if the
        # filesystem has insufficient headroom.
        needed=int(source.stat().st_size*2.5)+256*1024*1024
        if shutil.disk_usage(output.parent).free < needed:
            raise ValueError('迁移目标磁盘空间不足：需要预留旧库体积约 2.5 倍及 256 MiB')
        partial=Path(str(output)+'.partial')
        start=perf_counter()
        try:
            with closing(sqlite3.connect(partial)) as copy, copy:
                old.backup(copy,pages=2048,sleep=.1)
            with closing(sqlite3.connect(partial,timeout=120)) as db, db:
                if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower()!='delete':
                    raise ValueError('无法切换迁移副本至安全回滚日志模式')
                db.execute('PRAGMA foreign_keys=ON')
                ensure_raw_store(db)
                results=[]
                for item in candidates:
                    with db:
                        result=_compact_source(db,item['source_id'])
                    if result:results.append(result)
                health=raw_store_health(db)
                if health['dangling_refs']:
                    raise ValueError('压缩校验失败：存在无效的原始行引用')
                if db.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('压缩校验失败：外键不一致')
                # VACUUM actually releases freed TEXT pages into a smaller file.
                db.execute('VACUUM')
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('压缩校验失败：SQLite integrity_check 未通过')
                final_health=raw_store_health(db)
                if final_health['dangling_refs']:
                    raise ValueError('VACUUM 后原始行引用丢失')
            # Verify EVERY reconstructed original row and all unchanged V2
            # metadata before publishing the compacted copy. A failed audit
            # leaves the original untouched and publishes no destination.
            acceptance=verify(source,partial)
            # All SQLite connections to ``partial`` are now explicitly closed.
            # Publish with platform-safe no-clobber semantics; on Windows this
            # avoids deleting a freshly hard-linked but formerly open file.
            _publish_verified_copy(partial, output)
            return {'status':'created','source_bytes':source.stat().st_size,
                    'output_bytes':output.stat().st_size,'compact_sources':results,
                    'storage':final_health,'verification':{'sample_rows':acceptance['sample_rows'],
                    'sample_content_sha256':acceptance['sample_content_sha256'],
                    'metadata_tables_verified':len(acceptance['unchanged_tables'])},
                    'elapsed_seconds':round(perf_counter()-start,3),
                    'original_unchanged':True,'output':str(output)}
        except BaseException as failure:
            # Preserve the primary SQLite/verification error if a third-party
            # scanner temporarily locks a leftover .partial on Windows.
            cleanup_failures=[]
            for suffix in ('','-journal','-wal','-shm'):
                try:
                    Path(str(partial)+suffix).unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_failures.append(f'{suffix or "database"}: {cleanup_error}')
            if cleanup_failures and hasattr(failure, 'add_note'):
                failure.add_note('临时文件清理尚未完成：' + '; '.join(cleanup_failures))
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    options=parser.parse_args(argv)
    if not options.dry_run and options.output is None:
        parser.error('必须提供 --output 或 --dry-run')
    print(json.dumps(compact(options.source,options.output or options.source.with_suffix('.compact.sqlite'),
                             dry_run=options.dry_run),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
