"""Install an indexed-source calendar presentation VIEW without rewriting the 9M source samples.

Display base: 2026-01-01 00:00:00. This is deliberately synthetic display
format, not UTC measurement time. The original time_sort_num remains authoritative.
"""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

VIEW_SQL = """CREATE VIEW IF NOT EXISTS v2_samples_calendar AS
SELECT s.*,
CASE WHEN s.time_domain='numeric_unspecified' THEN
  strftime('%Y-%m-%d %H:%M:%f', '2026-01-01 00:00:00', printf('%+.6f seconds', s.time_sort_num))
WHEN s.time_domain='datetime_utc' THEN
  strftime('%Y-%m-%d %H:%M:%f', s.timestamp_raw)
ELSE s.timestamp_raw END AS display_timestamp
FROM v2_samples s"""

def install(path):
    path=Path(path)
    if not path.is_file(): raise FileNotFoundError(path)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA busy_timeout=30000')
        if conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone() != ('2',):
            raise ValueError('not a V2 database')
        conn.execute(VIEW_SQL)
        conn.commit()
        return conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--database',required=True)
    args=ap.parse_args()
    print('Rows protected (original samples unchanged):',install(args.database))
