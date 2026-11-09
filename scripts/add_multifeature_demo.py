"""Create deterministic, explicitly synthetic multivariate train/test tables.

Run from the AAS root: python -m scripts.add_multifeature_demo
Re-running updates only the two DEMO_ tables, never user tables.
"""
from __future__ import annotations
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / 'datasets' / 'history_sample.db'
TABLES = {'DEMO_multifeature_train': (480, False), 'DEMO_multifeature_test': (180, True)}


def create(db_path: Path = DB):
    with sqlite3.connect(db_path) as conn:
        for table, (count, faults) in TABLES.items():
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                # Do not overwrite a table after a user has edited it.
                continue
            conn.execute(f'''CREATE TABLE "{table}" (
                timestamp TEXT NOT NULL, model TEXT, subsystem TEXT, device TEXT,
                variable TEXT, value REAL, temperature REAL, vibration REAL,
                current REAL, pressure REAL, label INTEGER NOT NULL)''')
            origin = datetime(2026, 1, 1, 0, 0, 0) + (timedelta(days=1) if faults else timedelta(0))
            data = []
            for i in range(count):
                t = i / 12
                anomaly = faults and 85 <= i < 118
                temp = 24 + math.sin(t) * 1.5 + (9 if anomaly else 0)
                vib = .22 + .035 * math.cos(t*1.3) + (.35 if anomaly else 0)
                amp = 2.1 + .08*math.sin(t*.8) + (.7 if anomaly else 0)
                pressure = 101.3 + .9*math.cos(t/3) - (7 if anomaly else 0)
                stamp = (origin + timedelta(seconds=10*i)).strftime('%Y-%m-%d %H:%M:%S')
                data.append((stamp,'DEMO','ADCS','demo_unit','multisensor',
                    round(temp,5),round(temp,5),round(vib,5),round(amp,5),round(pressure,5),int(anomaly)))
            conn.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?,?,?,?,?,?,?,?)', data)
        conn.commit()


if __name__ == '__main__':
    create()
    print(f'Synthetic multifeature demo is available in {DB}')
