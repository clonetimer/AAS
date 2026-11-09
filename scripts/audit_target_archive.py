"""Read-only validation of full targets.zip and optionally a selected V2 DB."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
from scripts.target_archive_evidence import read_targets,summarize,check_annotated_database


def audit(path, db=None):
    path=Path(path).resolve(strict=True)
    records=read_targets(path)
    report=summarize(records)
    report['archive_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    if db is not None:
        report['database_check']=check_annotated_database(db,records)
    return report


def main():
    p=argparse.ArgumentParser(description='只读检查 targets.zip 的运行标签、划分、分组和来源映射')
    p.add_argument('--targets-zip',required=True,type=Path)
    p.add_argument('--database',type=Path,help='可选：严格比较已导入 V2 运行的原始成员哈希')
    p.add_argument('--report',type=Path,help='输出 JSON（不会修改 SQLite）')
    args=p.parse_args()
    report=audit(args.targets_zip,args.database)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report.get('database_check',{}).get('blocked'):
        raise SystemExit(2)

if __name__=='__main__': main()
