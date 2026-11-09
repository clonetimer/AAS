"""Rebuild a fresh V2 run database from selected CSV.gz and full targets.zip.

Matches each imported CSV by its unique token. All group/split evidence is
validated, but archive entries without imported telemetry are NOT materialized.
The existing destination is never overwritten.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from scripts.build_uploaded_v2 import build
from scripts.annotate_uploaded_targets_v2 import annotate_in_place
from scripts.migrate_v2_calendar_view import install as install_calendar_view


def rebuild(input_dir:Path, targets:Path, output:Path):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError(f'拒绝覆盖: {output}')
    output.parent.mkdir(parents=True,exist_ok=True)
    candidate=output.with_name(output.name+'.building')
    if candidate.exists():raise FileExistsError(f'上次构建临时文件仍存在，需人工检查: {candidate}')
    try:
        base=build(input_dir,candidate)
        report=annotate_in_place(candidate,targets)
        if base['samples']!=report['total_samples'] or base['run_count']!=report['runs']:
            raise RuntimeError('新库标签与原始采样量不一致')
        install_calendar_view(candidate)
        if output.exists():raise FileExistsError(f'目标文件已被其他进程创建: {output}')
        candidate.rename(output)
        return report
    finally:
        if candidate.exists():candidate.unlink()


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser()
    parser.add_argument('--input-dir',type=Path,default=root/'datasets/source_csv_gz')
    parser.add_argument('--targets',type=Path,default=root/'datasets/source_labels/targets.zip')
    parser.add_argument('--output',type=Path,default=root/'datasets/aas_telemetry_v2.sqlite')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    report=rebuild(args.input_dir,args.targets,args.output)
    if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sources'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
