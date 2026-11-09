"""Deduplicated lossless raw-row storage for V2 wide telemetry.

A v2_samples.raw_row_json of '' is an explicit reference to the *same source's*
v2_source_raw_rows(source_id, source_ordinal). Existing nonempty inline JSON
remains valid and requires no migration. The importer and the raw row table are
committed in the same transaction; source identities never cross datasets.
"""
from __future__ import annotations

import json
import sqlite3

DDL = '''CREATE TABLE IF NOT EXISTS v2_source_raw_rows (
    source_id INTEGER NOT NULL REFERENCES v2_sources(id) ON DELETE CASCADE,
    source_ordinal INTEGER NOT NULL,
    raw_row_json TEXT NOT NULL,
    PRIMARY KEY(source_id, source_ordinal)
) WITHOUT ROWID'''


def ensure_raw_store(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    # A stable read-only relation for users who need lossless source rows from
    # mixed inline and shared representations without Python helpers.
    conn.execute('''CREATE VIEW IF NOT EXISTS v2_sample_raw_resolved AS
        SELECT s.id AS sample_id,
          CASE WHEN s.raw_row_json='' THEN r.raw_row_json ELSE s.raw_row_json END AS raw_row_json
        FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        LEFT JOIN v2_source_raw_rows r ON r.source_id=g.source_id
            AND r.source_ordinal=s.source_ordinal''')


def resolved_raw_row(conn: sqlite3.Connection, sample_id: int) -> str:
    """Read raw JSON through either the old inline or new shared representation.

    Fail closed on broken references: never interpret an empty placeholder as
    a valid raw row. Read-only; preserves original JSON bytes/field spelling.
    """
    row = conn.execute('''SELECT s.raw_row_json, r.raw_row_json
        FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
        LEFT JOIN v2_source_raw_rows r ON r.source_id=g.source_id
            AND r.source_ordinal=s.source_ordinal
        WHERE s.id=?''', (sample_id,)).fetchone() if has_raw_store(conn) else None
    if row is None:
        old = conn.execute('SELECT raw_row_json FROM v2_samples WHERE id=?', (sample_id,)).fetchone()
        if not old: raise ValueError('样本不存在')
        if old[0] == '': raise ValueError('原始遥测行引用缺失，请检查数据库完整性')
        return old[0]
    inline, shared = row
    if inline != '': return inline
    if shared is None: raise ValueError('原始遥测行引用缺失，请检查数据库完整性')
    return shared


def has_raw_store(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_source_raw_rows'").fetchone() is not None


def raw_store_health(conn: sqlite3.Connection) -> dict:
    if not has_raw_store(conn):
        missing = conn.execute("SELECT COUNT(*) FROM v2_samples WHERE raw_row_json='' ").fetchone()[0]
        return {'shared_rows': 0, 'references': missing, 'dangling_refs': missing}
    shared = conn.execute('SELECT COUNT(*) FROM v2_source_raw_rows').fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM v2_samples WHERE raw_row_json='' ").fetchone()[0]
    bad = conn.execute('''SELECT COUNT(*) FROM v2_samples s
        JOIN v2_segments g ON g.id=s.segment_id
        LEFT JOIN v2_source_raw_rows r ON r.source_id=g.source_id AND r.source_ordinal=s.source_ordinal
        WHERE s.raw_row_json='' AND r.source_id IS NULL''').fetchone()[0]
    return {'shared_rows': shared, 'references': total, 'dangling_refs': bad}
