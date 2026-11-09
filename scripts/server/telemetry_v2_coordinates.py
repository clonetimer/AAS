"""Add persistent frame coordinates to a V2 database without rewriting samples.

The V2 schema version stays at 2 because older readers can still read every
historical time sample. Frame-aware readers check for these columns explicitly.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3


def legacy_fingerprint(conn: sqlite3.Connection) -> dict:
    """Hash the original sample fields in stable ID order, including truth labels."""
    fields = ('id', 'segment_id', 'channel_id', 'source_ordinal',
              'timestamp_raw', 'timestamp_type', 'time_domain', 'time_sort_text',
              'time_sort_num', 'time_utc_us', 'value_num', 'value_raw_json',
              'label_int', 'label_raw_json', 'raw_row_json')
    digest = hashlib.sha256()
    count = 0
    labels = {None: 0, 0: 0, 1: 0}
    def canonical(value):
        if isinstance(value, bytes):
            return {'blob_hex': value.hex()}
        if isinstance(value, float) and not math.isfinite(value):
            return {'float': repr(value)}
        return value
    for row in conn.execute('SELECT ' + ','.join(fields) + ' FROM v2_samples ORDER BY id'):
        values = tuple(row)
        digest.update(json.dumps([canonical(value) for value in values], ensure_ascii=False, separators=(',', ':'),
                                 allow_nan=False).encode('utf-8'))
        digest.update(b'\n')
        count += 1
        labels[values[12]] = labels.get(values[12], 0) + 1
    return {'samples': count, 'labels': {str(k): v for k, v in labels.items()},
            'legacy_sha256': digest.hexdigest()}


def ensure_coordinate_schema(conn: sqlite3.Connection) -> None:
    """Idempotent additive migration; caller owns the transaction."""
    version = conn.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
    if not version or str(version[0]) != '2':
        raise ValueError('不是有效的 V2 数据库，拒绝迁移')
    segment_cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_segments)')}
    sample_cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_samples)')}
    if 'coordinate_mode' not in segment_cols:
        conn.execute("ALTER TABLE v2_segments ADD COLUMN coordinate_mode TEXT NOT NULL DEFAULT 'time' CHECK(coordinate_mode IN ('time','frame'))")
    if 'frame_sequence_id' not in segment_cols:
        conn.execute('ALTER TABLE v2_segments ADD COLUMN frame_sequence_id TEXT')
    if 'frame_index' not in sample_cols:
        conn.execute('ALTER TABLE v2_samples ADD COLUMN frame_index INTEGER CHECK(frame_index IS NULL OR frame_index >= 1)')
    if 'relative_seconds' not in sample_cols:
        conn.execute('ALTER TABLE v2_samples ADD COLUMN relative_seconds REAL')
    if 'quality_flag' not in sample_cols:
        conn.execute("ALTER TABLE v2_samples ADD COLUMN quality_flag TEXT CHECK(quality_flag IN ('ok','missing') OR quality_flag IS NULL)")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_v2_segments_coordinate '
                 'ON v2_segments(coordinate_mode,frame_sequence_id,role)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_v2_sample_frame '
                 'ON v2_samples(segment_id,frame_index) WHERE frame_index IS NOT NULL')
    triggers = ('''
    CREATE TRIGGER IF NOT EXISTS v2_frame_segment_insert
    BEFORE INSERT ON v2_segments WHEN NEW.coordinate_mode='frame'
      AND (NEW.frame_sequence_id IS NULL OR length(trim(NEW.frame_sequence_id))=0)
    BEGIN SELECT RAISE(ABORT,'frame sequence id required'); END
    ''', '''CREATE TRIGGER IF NOT EXISTS v2_frame_segment_update
    BEFORE UPDATE OF coordinate_mode,frame_sequence_id ON v2_segments
    WHEN NEW.coordinate_mode='frame'
      AND (NEW.frame_sequence_id IS NULL OR length(trim(NEW.frame_sequence_id))=0)
    BEGIN SELECT RAISE(ABORT,'frame sequence id required'); END
    ''', '''CREATE TRIGGER IF NOT EXISTS v2_frame_sample_insert
    BEFORE INSERT ON v2_samples
    WHEN (SELECT coordinate_mode FROM v2_segments WHERE id=NEW.segment_id)='frame'
      AND NEW.frame_index IS NULL
    BEGIN SELECT RAISE(ABORT,'frame index required'); END
    ''', '''CREATE TRIGGER IF NOT EXISTS v2_frame_sample_update
    BEFORE UPDATE OF frame_index,segment_id ON v2_samples
    WHEN (SELECT coordinate_mode FROM v2_segments WHERE id=NEW.segment_id)='frame'
      AND NEW.frame_index IS NULL
    BEGIN SELECT RAISE(ABORT,'frame index required'); END
    ''')
    for statement in triggers:
        conn.execute(statement)
