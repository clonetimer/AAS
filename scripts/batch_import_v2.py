#!/usr/bin/env python3
"""Batch CSV/CSV.GZ into an installed AAS V2 database (local administrator only)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from scripts.server.telemetry_v2_batch_import import run_manifest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True,help='Existing V2 SQLite file')
    p.add_argument('--manifest',type=Path,required=True,help='Manifest JSON with file-relative paths and config')
    p.add_argument('--dry-run',action='store_true',help='Validate without changing the destination')
    p.add_argument('--stop-on-error',action='store_true')
    p.add_argument('--max-rows',type=int,default=2_000_000)
    p.add_argument('--max-extracted-bytes',type=int,default=1_000_000_000)
    p.add_argument('--report',type=Path,help='Write a redacted batch result JSON')
    args=p.parse_args(argv)
    try:
        result=run_manifest(args.database,args.manifest,dry_run=args.dry_run,
                            stop_on_error=args.stop_on_error,max_rows=args.max_rows,
                            max_extracted_bytes=args.max_extracted_bytes)
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'FATAL: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.report:
        # This report is task metadata only: never raw samples or secret config.
        args.report.parent.mkdir(parents=True,exist_ok=True)
        import os,tempfile
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=args.report.parent,delete=False) as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
            staged=f.name
        os.replace(staged,args.report)
    return 0 if result['status']=='complete' else 1

if __name__=='__main__':raise SystemExit(main())
