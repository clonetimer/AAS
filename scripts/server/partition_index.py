"""Optional, verified time-coverage cache for single-channel SQLite fragments.

An explicit migration installs a source-table change trigger for each partition.
When a writer modifies rows, the cached bounds are marked dirty *in the same
transaction*. Unregistered, dropped, or dirty tables are always rescanned,
never silently excluded on the strength of a stale cache. No sample is copied.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from scripts.server import series_catalog as catalog

TABLE = 'aas_series_partitions'
TRIGGER_OPS = ('INSERT', 'UPDATE', 'DELETE')


def _exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _trigger(table: str, operation: str) -> str:
    return 'aas_pidx_' + hashlib.sha256(table.encode('utf-8')).hexdigest()[:20] + '_' + operation[0].lower()


def _has_triggers(conn: sqlite3.Connection, table: str) -> bool:
    return all(conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=? AND tbl_name=?", (
        _trigger(table, op), table)).fetchone() for op in TRIGGER_OPS)


def init(conn: sqlite3.Connection) -> None:
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE} (
      table_name TEXT PRIMARY KEY,
      model TEXT NOT NULL, subsystem TEXT NOT NULL, device TEXT NOT NULL, variable TEXT NOT NULL,
      time_start, time_end, row_count INTEGER NOT NULL CHECK(row_count >= 0),
      timestamp_kind TEXT NOT NULL, dirty INTEGER NOT NULL DEFAULT 0 CHECK(dirty IN (0,1))
    )''')
    conn.execute(f'''CREATE INDEX IF NOT EXISTS aas_partition_identity_time_idx
      ON {TABLE}(model,subsystem,device,variable,time_start,time_end)''')


def eligible(conn: sqlite3.Connection, name: str) -> dict[str, str] | None:
    if not name or name.startswith(('aas_', 'sqlite_')) or name.lower().endswith('_clean'):
        return None
    cols = set(catalog.columns(conn, name))
    if not {'timestamp', 'value', 'label'} <= cols:
        return None
    if {column.lower() for column in cols} - (catalog.SAMPLE_FIELDS | set(catalog.IDENTITY)):
        return None
    return catalog.signature(conn, name)


def _summarize(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = catalog.quote(table)
    row = conn.execute(f'SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM {quoted}').fetchone()
    if not row[2]:
        kind = 'empty'
    else:
        kinds = {r[0] for r in conn.execute(f'SELECT DISTINCT typeof(timestamp) FROM {quoted}')}
        if kinds <= {'integer', 'real'}:
            kind = 'number'
        elif kinds == {'text'}:
            kind = 'text'
        else:
            raise ValueError(f'{table} 含空值或混合类型时间戳')
    return dict(table=table, time_start=row[0], time_end=row[1], row_count=int(row[2]),
                timestamp_kind=kind)


def refresh(conn: sqlite3.Connection, *, with_timestamp_indexes: bool = False) -> dict[str, Any]:
    """Refresh metadata and DML triggers under caller's SQLite transaction.

    Intended for an explicit administrative action, never an implicit GET.
    Creating physical timestamp indexes is opt-in because it can take time.
    """
    conn.execute('SAVEPOINT refresh_partitions')
    try:
        init(conn)
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        indexed = []
        for name in names:
            identity = eligible(conn, name)
            if identity is None:
                continue
            info = _summarize(conn, name)
            conn.execute(f'''INSERT INTO {TABLE}
               (table_name,model,subsystem,device,variable,time_start,time_end,row_count,timestamp_kind,dirty)
               VALUES (?,?,?,?,?,?,?,?,?,0)
               ON CONFLICT(table_name) DO UPDATE SET
                 model=excluded.model,subsystem=excluded.subsystem,device=excluded.device,
                 variable=excluded.variable,time_start=excluded.time_start,
                 time_end=excluded.time_end,row_count=excluded.row_count,
                 timestamp_kind=excluded.timestamp_kind,dirty=0''',
                 (name, *(identity[k] for k in catalog.IDENTITY), info['time_start'],
                  info['time_end'], info['row_count'], info['timestamp_kind']))
            for operation in TRIGGER_OPS:
                trigger = catalog.quote(_trigger(name, operation))
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation} ON {catalog.quote(name)}
                    BEGIN UPDATE {TABLE} SET dirty=1 WHERE table_name={_sql_literal(name)}; END''')
            if with_timestamp_indexes:
                idx = catalog.quote('aas_ts_' + hashlib.sha256(name.encode()).hexdigest()[:20])
                conn.execute(f'CREATE INDEX IF NOT EXISTS {idx} ON {catalog.quote(name)}(timestamp)')
            indexed.append(name)
        if indexed:
            conn.execute(f'DELETE FROM {TABLE} WHERE table_name NOT IN ({",".join("?" for _ in indexed)})', indexed)
        else:
            conn.execute(f'DELETE FROM {TABLE}')
        conn.execute('RELEASE SAVEPOINT refresh_partitions')
        return {'indexed_tables': len(indexed), 'time_indexes': with_timestamp_indexes,
                'tables': indexed}
    except BaseException:
        conn.execute('ROLLBACK TO SAVEPOINT refresh_partitions')
        conn.execute('RELEASE SAVEPOINT refresh_partitions')
        raise


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def inspect(conn: sqlite3.Connection, table: str, identity: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """Return verified summary and a flag indicating whether cache was trusted."""
    if _exists(conn, TABLE):
        row = conn.execute(f'''SELECT model,subsystem,device,variable,time_start,time_end,
                            row_count,timestamp_kind,dirty FROM {TABLE} WHERE table_name=?''', (table,)).fetchone()
        if (row and not row[8] and _has_triggers(conn, table)
                and tuple(identity[k] for k in catalog.IDENTITY) == tuple(row[:4])):
            return dict(table=table, time_start=row[4], time_end=row[5],
                        row_count=row[6], timestamp_kind=row[7]), True
    return _summarize(conn, table), False
