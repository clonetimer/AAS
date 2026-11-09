"""Offline, backup-first migration of the old flat catalog to a hierarchy.

Does not modify measurement tables. Run with server stopped. Dry-run default.
Backups are made through SQLite backup API, not file copies, to include WAL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.server import series_catalog as catalog  # pylint: disable=wrong-import-position


def _snapshot(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Capture exact physical sample rows and logical channel identities."""
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ) if not row[0].startswith('aas_')]
    samples = {}
    for name in names:
        digest = hashlib.sha256()
        count = 0
        cur = conn.execute(f'SELECT * FROM {catalog.quote(name)} ORDER BY rowid')
        for row in cur:
            digest.update((json.dumps(row, ensure_ascii=False, default=str,
                                      separators=(',', ':')) + '\n').encode('utf-8'))
            count += 1
        samples[name] = (count, digest.hexdigest(), tuple(catalog.columns(conn, name)))
    registered = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (catalog.CATALOG,)).fetchone():
        for (name,) in conn.execute(f'SELECT table_name FROM {catalog.CATALOG}'):
            registered[name] = catalog.get(conn, name)
    return samples, registered


def migrate(db_path: Path, *, apply: bool = False,
            backup_dir: Path | None = None) -> dict:
    target = db_path.resolve(strict=True)
    if target.parent != (ROOT / 'datasets').resolve() or target.suffix.lower() not in {'.db','.sqlite','.sqlite3'}:
        raise ValueError('仅允许迁移 datasets 目录下的 SQLite 数据库')
    with sqlite3.connect(target) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        preview = catalog.normalize_hierarchy(conn, dry_run=True)
        if not apply:
            return {'database': target.name, **preview}
        if preview['already_normalized']:
            return {'database': target.name, 'converted': 0,
                    'already_normalized': True, 'backup': None}
        output = (backup_dir or target.parent / 'backups').resolve()
        output.mkdir(parents=True, exist_ok=True)
        backup = output / f'{target.name}.before-hierarchy-normalization.bak'
        if backup.exists():
            raise FileExistsError(f'拒绝覆盖已有备份：{backup}')
        with sqlite3.connect(backup) as destination:
            conn.backup(destination)
        before = _snapshot(conn)
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = catalog.normalize_hierarchy(conn, dry_run=False)
            if _snapshot(conn) != before:
                raise ValueError('迁移前后原始数据或元信息不一致，已回滚')
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite 完整性检查未通过')
            catalog.validate_hierarchy(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {'database': target.name, 'converted': result['converted'],
            'sample_tables_verified': len(before[0]),
            'sample_rows_verified': sum(record[0] for record in before[0].values()),
            'backup': str(backup)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    print(json.dumps(migrate(db, apply=args.apply, backup_dir=args.backup_dir),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
