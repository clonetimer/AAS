"""Create an empty canonical V2 database for a fresh installation."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from scripts.rebuild_telemetry_v2 import create_v2


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(argv)
    target=args.out.resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise FileExistsError(f'数据库已存在，不会覆盖：{target}')
    conn=sqlite3.connect(target)
    try:
        create_v2(conn)
        conn.commit()
    except BaseException:
        conn.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    print(target)


if __name__=='__main__':main()
