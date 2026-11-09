"""Read-only post-deployment check for a V2 database."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def check(path: Path):
    with sqlite3.connect(str(path)) as conn:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError('SQLite integrity_check failed: ' + str(integrity))
        segment_cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_segments)')}
        sample_cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_samples)')}
        if not {'coordinate_mode', 'frame_sequence_id'} <= segment_cols:
            raise RuntimeError('v2_segments lacks dual-coordinate columns')
        if not {'timestamp_raw', 'source_ordinal', 'frame_index', 'relative_seconds', 'quality_flag'} <= sample_cols:
            raise RuntimeError('v2_samples lacks dual-coordinate columns')
        return {
            'database': str(path.resolve()),
            'integrity_check': integrity,
            'sources': conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0],
            'segments': conn.execute('SELECT COUNT(*) FROM v2_segments').fetchone()[0],
            'samples': conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0],
            'coordinate_modes': dict(conn.execute(
                'SELECT coordinate_mode, COUNT(*) FROM v2_segments GROUP BY coordinate_mode')),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.db), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
