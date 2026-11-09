"""Explicit time-interval ground truth, independent from sampling frequency.

Each record represents either a fault event (label=1) or a *verified normal*
annotation (label=0). Uncovered time is UNKNOWN, never implicitly normal.
Intervals are half-open [start, end); timestamp kinds cannot be mixed within
one source table. Original sensor readings and row-level labels are untouched.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import math
import sqlite3
from typing import Any

# Reuse the fusion engine's timestamp normalization to guarantee matching units.
from scripts.core.multi_table_fusion import _as_seconds, _moment

EVENT_TABLE = 'aas_fault_intervals'
MAX_EVENTS_PER_SOURCE = 10_000


def _exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (EVENT_TABLE,)).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS aas_fault_intervals (
      id INTEGER PRIMARY KEY,
      table_name TEXT NOT NULL,
      start_timestamp TEXT NOT NULL,
      end_timestamp TEXT NOT NULL,
      timestamp_kind TEXT NOT NULL CHECK (timestamp_kind IN ('numeric','local','utc')),
      start_seconds REAL NOT NULL,
      end_seconds REAL NOT NULL,
      label INTEGER NOT NULL CHECK(label IN (0, 1)),
      fault_type TEXT NOT NULL DEFAULT '',
      notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      CHECK(end_seconds > start_seconds)
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_aas_fault_intervals_source
                 ON aas_fault_intervals(table_name,timestamp_kind,start_seconds,end_seconds)''')


def _validate_source(conn: sqlite3.Connection, table: str) -> None:
    if not isinstance(table, str) or not table.strip() or len(table) > 256:
        raise ValueError('请选择有效的数据表')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        raise ValueError(f'数据表不存在：{table}')
    if table.lower().startswith(('aas_', 'sqlite_')):
        raise ValueError('不能为系统目录添加故障事件')
    quoted = '"' + table.replace('"', '""') + '"'
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info({quoted})')}
    if not {'timestamp', 'value'}.issubset(cols):
        raise ValueError('事件必须关联具有 timestamp 和 value 的采样表')


def list_intervals(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    _validate_source(conn, table)
    if not _exists(conn):
        return []
    result = conn.execute('''SELECT id, table_name, start_timestamp, end_timestamp,
        timestamp_kind, label, fault_type, notes, created_at
        FROM aas_fault_intervals WHERE table_name=? ORDER BY start_seconds,id''', (table,)).fetchall()
    keys = ('id', 'table_name', 'start_timestamp', 'end_timestamp', 'timestamp_kind',
            'label', 'fault_type', 'notes', 'created_at')
    return [dict(zip(keys, row)) for row in result]


def create_interval(conn: sqlite3.Connection, *, table: str, start: Any,
                    end: Any, label: int, fault_type: str = '', notes: str = '') -> dict[str, Any]:
    _validate_source(conn, table)
    start_key, end_key = _moment(start), _moment(end)
    if start_key[0] != end_key[0]:
        raise ValueError('事件开始与结束时间戳类型不一致')
    lo, hi = _as_seconds(start_key), _as_seconds(end_key)
    if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
        raise ValueError('事件必须满足开始时间早于结束时间；使用左闭右开区间 [start,end)')
    if type(label) is not int or label not in (0, 1):
        raise ValueError('事件标签只能为 0（已确认正常）或 1（故障）')
    if not isinstance(fault_type, str) or len(fault_type) > 120 or not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError('事件类型或备注过长')
    fault_type, notes = fault_type.strip(), notes.strip()
    if label == 0 and fault_type:
        raise ValueError('正常区间不能带故障类型')
    quoted = '"' + table.replace('"', '""') + '"'
    first_sample = conn.execute(f'SELECT timestamp FROM {quoted} WHERE timestamp IS NOT NULL LIMIT 1').fetchone()
    if first_sample and _moment(first_sample[0])[0] != start_key[0]:
        raise ValueError('事件与采样数据的时间戳类型不一致（时间戳类型必须一致）')
    ensure_schema(conn)
    if conn.execute('SELECT 1 FROM aas_fault_intervals WHERE table_name=? AND timestamp_kind<>? LIMIT 1',
                    (table, start_key[0])).fetchone():
        raise ValueError('同一数据表的事件时间戳类型必须一致')
    if conn.execute('''SELECT 1 FROM aas_fault_intervals WHERE table_name=?
                      AND start_seconds < ? AND end_seconds > ? LIMIT 1''', (table, hi, lo)).fetchone():
        raise ValueError('区间与已有故障或已确认正常事件重叠，请调整边界')
    if conn.execute('SELECT COUNT(*) FROM aas_fault_intervals WHERE table_name=?', (table,)).fetchone()[0] >= MAX_EVENTS_PER_SOURCE:
        raise ValueError('单表故障/正常区间数量达到上限')
    cur = conn.execute('''INSERT INTO aas_fault_intervals
      (table_name,start_timestamp,end_timestamp,timestamp_kind,start_seconds,end_seconds,label,fault_type,notes)
      VALUES (?,?,?,?,?,?,?,?,?)''',
      (table, str(start), str(end), start_key[0], lo, hi, label, fault_type, notes))
    return {'id': cur.lastrowid, 'table_name': table, 'label': label,
            'start_timestamp': str(start), 'end_timestamp': str(end),
            'fault_type': fault_type, 'notes': notes}



def update_interval(conn: sqlite3.Connection, *, table: str, interval_id: int,
                    start: Any, end: Any, label: int,
                    fault_type: str = '', notes: str = '') -> dict[str, Any]:
    """Atomically replace one annotation, preserving its stable event ID."""
    _validate_source(conn, table)
    if type(interval_id) is not int or interval_id <= 0:
        raise ValueError('事件 ID 无效')
    if not _exists(conn) or not conn.execute(
        'SELECT 1 FROM aas_fault_intervals WHERE table_name=? AND id=?',
        (table, interval_id)
    ).fetchone():
        raise ValueError('事件不存在或不属于选中的数据表')
    # SAVEPOINT guarantees the original survives validation or insertion errors.
    with conn:
        conn.execute('SAVEPOINT event_edit')
        try:
            conn.execute('DELETE FROM aas_fault_intervals WHERE table_name=? AND id=?',
                         (table, interval_id))
            new = create_interval(conn, table=table, start=start, end=end, label=label,
                                  fault_type=fault_type, notes=notes)
            conn.execute('UPDATE aas_fault_intervals SET id=? WHERE id=?',
                         (interval_id, new['id']))
            conn.execute('RELEASE SAVEPOINT event_edit')
            return {**new, 'id': interval_id}
        except Exception:
            conn.execute('ROLLBACK TO SAVEPOINT event_edit')
            conn.execute('RELEASE SAVEPOINT event_edit')
            raise


def delete_interval(conn: sqlite3.Connection, *, table: str, interval_id: int) -> None:
    _validate_source(conn, table)
    if type(interval_id) is not int or interval_id <= 0:
        raise ValueError('事件 ID 无效')
    if not _exists(conn) or conn.execute(
        'DELETE FROM aas_fault_intervals WHERE table_name=? AND id=?', (table, interval_id)
    ).rowcount != 1:
        raise ValueError('事件不存在或不属于选中的数据表')


def delete_source_intervals(conn: sqlite3.Connection, table: str) -> None:
    """Call within the same transaction when an associated physical table is deleted."""
    if _exists(conn):
        conn.execute('DELETE FROM aas_fault_intervals WHERE table_name=?', (table,))


@dataclass
class IntervalLookup:
    """Fast immutable point query; None means unannotated/unknown."""
    starts: list[float]
    ends: list[float]
    labels: list[int]
    kind: str | None
    fingerprint: str = ""

    def at(self, key: tuple[str, float | str]) -> int | None:
        if self.kind is None or key[0] != self.kind:
            return None
        point = _as_seconds(key)
        i = bisect_right(self.starts, point) - 1
        if i >= 0 and point < self.ends[i]:
            return self.labels[i]
        return None


def load_lookup(conn: sqlite3.Connection, table: str) -> IntervalLookup:
    _validate_source(conn, table)
    if not _exists(conn):
        return IntervalLookup([], [], [], None)
    rows = conn.execute('''SELECT timestamp_kind,start_seconds,end_seconds,label
        FROM aas_fault_intervals WHERE table_name=? ORDER BY start_seconds''', (table,)).fetchall()
    if len(rows) > MAX_EVENTS_PER_SOURCE:
        raise ValueError(f'{table} 事件区间超过查询上限')
    kinds = {r[0] for r in rows}
    if len(kinds) > 1:
        raise ValueError(f'{table} 的事件包含混合时间戳类型')
    previous = float('-inf')
    for _, start, end, _label in rows:
        if start < previous or end <= start:
            raise ValueError(f'{table} 的事件区间出现交叠或无效边界')
        previous = end
    fingerprint = hashlib.sha256(repr([tuple(r) for r in rows]).encode('utf-8')).hexdigest()
    return IntervalLookup([r[1] for r in rows], [r[2] for r in rows],
                          [r[3] for r in rows], next(iter(kinds)) if kinds else None,
                          fingerprint)
