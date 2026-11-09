"""Offline, backup-first migration of constant channel identity to aas_series_catalog.

Run with the web service STOPPED. Dry-run is default. Existing multi-sensor,
cleaned and nonstandard tables remain untouched; do not delete the backup.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.server.series_catalog import migrate_eligible, logical_columns, expand, quote

ROOT = PROJECT_ROOT


def _digest(conn: sqlite3.Connection, table: str, *, projected: bool) -> tuple[int, str]:
    conn.row_factory = sqlite3.Row
    hasher = hashlib.sha256()
    count = 0
    fields = logical_columns(conn, table) if projected else [row[1] for row in conn.execute(f'PRAGMA table_info({quote(table)})')]
    for row in conn.execute(f'SELECT * FROM {quote(table)} ORDER BY rowid'):
        values = expand(conn, table, dict(row)) if projected else dict(row)
        # Hash a stable serialization without coercing numeric values to strings.
        import json
        hasher.update((json.dumps([values[field] for field in fields], ensure_ascii=False,
                                  separators=(',', ':'), default=str) + '\n').encode('utf-8'))
        count += 1
    return count, hasher.hexdigest()


def migrate(db_path: Path, *, apply: bool = False, backup_dir: Path | None = None) -> dict:
    source = db_path.resolve(strict=True)
    if source.parent != (ROOT / 'datasets').resolve() or source.suffix.lower() not in {'.db', '.sqlite', '.sqlite3'}:
        raise ValueError('只能迁移本工程 datasets 目录下的 SQLite 数据库')
    with sqlite3.connect(source) as conn:
        preview = migrate_eligible(conn, dry_run=True)
        if not apply:
            return {'database': source.name, 'dry_run': True, 'eligible': len(preview['converted']),
                    'skipped': preview['skipped']}
        if not preview['converted']:
            return {'database': source.name, 'converted': 0, 'backup': None}
        output = (backup_dir or (source.parent / 'backups')).resolve()
        output.mkdir(parents=True, exist_ok=True)
        backup = output / f'{source.name}.before-series-normalization.bak'
        if backup.exists():
            raise FileExistsError(f'备份已存在，拒绝覆盖：{backup}')
        with sqlite3.connect(backup) as dest:
            conn.backup(dest)
        before = {name: _digest(conn, name, projected=False) for name in preview['converted']}
        conn.execute('BEGIN IMMEDIATE')
        try:
            outcome = migrate_eligible(conn, dry_run=False)
            failures = {key: msg for key, msg in outcome['skipped'].items() if key in before}
            if failures:
                raise ValueError(f'迁移时出现结构问题，已回滚：{failures}')
            for name, old in before.items():
                now = _digest(conn, name, projected=True)
                if now != old:
                    raise ValueError(f'{name} 迁移前后采样值、时间戳或标签不一致')
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite 完整性检查失败')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {'database': source.name, 'converted': len(preview['converted']),
            'backup': str(backup), 'records_verified': sum(x[0] for x in before.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='datasets 下的数据库路径')
    parser.add_argument('--apply', action='store_true', help='备份后执行；默认只预览')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    target = Path(args.db)
    if not target.is_absolute():
        target = ROOT / target
    print(migrate(target, apply=args.apply, backup_dir=args.backup_dir))


if __name__ == '__main__':
    main()
