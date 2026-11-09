"""Incrementally add frame coordinates to an existing V2 SQLite database.

Run from the repository root:
  python -m scripts.migrate_v2_coordinates --db path/to/v2.sqlite
  python -m scripts.migrate_v2_coordinates --db path/to/v2.sqlite --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from scripts.server.telemetry_v2_coordinates import ensure_coordinate_schema, legacy_fingerprint


def migrate(path: Path, *, apply: bool = False) -> dict:
    path = Path(path).resolve(strict=True)
    uri = path.as_uri() + ('?mode=rw' if apply else '?mode=ro')
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        before = legacy_fingerprint(conn)
        if not apply:
            return {'database': str(path), 'applied': False, 'before': before}
        conn.execute('BEGIN IMMEDIATE')
        try:
            prior_columns = {row[1] for row in conn.execute('PRAGMA table_info(v2_samples)')}
            existing_frame_count = (conn.execute('SELECT COUNT(*) FROM v2_samples WHERE frame_index IS NOT NULL').fetchone()[0]
                                    if 'frame_index' in prior_columns else 0)
            ensure_coordinate_schema(conn)
            after = legacy_fingerprint(conn)
            if after != before:
                raise RuntimeError('迁移前后历史记录、标签或内容哈希不一致')
            if not existing_frame_count:
                bad = conn.execute("SELECT COUNT(*) FROM v2_segments WHERE coordinate_mode!='time' OR frame_sequence_id IS NOT NULL").fetchone()[0]
                if bad:
                    raise RuntimeError('历史段坐标模式异常')
                bad = conn.execute('SELECT COUNT(*) FROM v2_samples WHERE frame_index IS NOT NULL OR relative_seconds IS NOT NULL').fetchone()[0]
                if bad:
                    raise RuntimeError('历史样本帧/相对时间字段异常')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return {'database': str(path), 'applied': True, 'before': before, 'after': after}
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(migrate(args.db, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
