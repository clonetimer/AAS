#!/usr/bin/env python
"""
清理输出目录脚本。支持按时间戳、数量、或全清理。

用法：
  python scripts/clean_outputs.py --keep-latest 10      # 保留最近10次实验
  python scripts/clean_outputs.py --keep-recent 3600    # 保留最近3600秒内的实验
  python scripts/clean_outputs.py --dry-run              # 只显示将删除的内容
  python scripts/clean_outputs.py --cleanup-all          # 删除所有输出
"""

import argparse
import shutil
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Tuple


def parse_experiment_metadata(exp_dir: Path) -> Tuple[str, datetime]:
    """解析实验元数据，返回 (exp_id, created_at)"""
    metadata_file = exp_dir / "metadata.json"
    if not metadata_file.exists():
        return exp_dir.name, None

    try:
        with open(metadata_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        timestamp_str = meta.get("created_at", "")
        # ISO 格式: 2026-04-08T21:47:56.669046
        created_at = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00').split('.')[0])
        return meta.get("experiment_id", exp_dir.name), created_at
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        print(f"⚠️  无法解析 {exp_dir.name} 的元数据: {e}")
        return exp_dir.name, None


def list_experiments(output_dir: Path) -> List[Tuple[Path, str, datetime]]:
    """列表所有实验，格式 (目录路径, 实验ID, 创建时间)"""
    experiments = []
    if not output_dir.exists():
        print(f"❌ 输出目录不存在: {output_dir}")
        return experiments

    for exp_dir in sorted(output_dir.iterdir()):
        if exp_dir.is_dir() and exp_dir.name.startswith("exp_"):
            exp_id, created_at = parse_experiment_metadata(exp_dir)
            experiments.append((exp_dir, exp_id, created_at))

    return experiments


def keep_latest_experiments(experiments: List[Tuple], keep_count: int) -> List[Path]:
    """保留最新的 N 个实验，返回待删除的目录列表"""
    # 按时间戳排序（最新的在后）
    sorted_exps = sorted(experiments, key=lambda x: x[2] or datetime.min)

    # 找出待删除的
    to_delete = sorted_exps[:-keep_count] if len(sorted_exps) > keep_count else []
    return [exp[0] for exp in to_delete]


def keep_recent_experiments(experiments: List[Tuple], seconds: int) -> List[Path]:
    """保留最近 N 秒内创建的实验，返回待删除的目录列表"""
    cutoff_time = datetime.now() - timedelta(seconds=seconds)
    to_delete = [exp[0] for exp in experiments if exp[2] and exp[2] < cutoff_time]
    return to_delete


def calculate_size(path: Path) -> float:
    """计算目录大小，单位 MB"""
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def display_experiments(experiments: List[Tuple], to_delete: List[Path] = None):
    """显示实验列表与统计"""
    print("\n📊 实验总览:")
    print(f"{'实验ID':<25} {'创建时间':<25} {'大小 (MB)':<12} {'状态':<10}")
    print("-" * 72)

    total_size = 0
    delete_size = 0
    to_delete_set = set(to_delete) if to_delete else set()

    for exp_dir, exp_id, created_at in experiments:
        size = calculate_size(exp_dir)
        status = "🗑️  待删" if exp_dir in to_delete_set else "✅ 保留"
        time_str = created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else "未知"
        print(f"{exp_id:<25} {time_str:<25} {size:<12.2f} {status:<10}")

        total_size += size
        if exp_dir in to_delete_set:
            delete_size += size

    print("-" * 72)
    print(f"总大小: {total_size:.2f} MB | 将删除: {delete_size:.2f} MB | 将保留: {total_size - delete_size:.2f} MB")


def cleanup(output_dir: Path, to_delete: List[Path], dry_run: bool = False):
    """执行清理"""
    del output_dir
    if not to_delete:
        print("✅ 无需清理")
        return 0

    print(f"\n🧹 清理计划: 将删除 {len(to_delete)} 个实验目录")

    if dry_run:
        print("(干运行模式 - 不实际删除)")

    deleted_count = 0
    for path in to_delete:
        try:
            if dry_run:
                print(f"  [DRY] 删除 {path.name}")
            else:
                shutil.rmtree(path)
                print(f"  ✓ 已删除 {path.name}")
            deleted_count += 1
        except OSError as e:
            print(f"  ❌ 删除失败 {path.name}: {e}")

    print(f"\n✅ 清理完成: 删除了 {deleted_count} 个目录")
    return deleted_count


def main():
    parser = argparse.ArgumentParser(
        description="清理输出目录中的实验结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/clean_outputs.py --keep-latest 10      # 保留最新的10个实验
  python scripts/clean_outputs.py --keep-recent 86400   # 保留最近24小时的实验
  python scripts/clean_outputs.py --dry-run              # 预览将删除的内容
  python scripts/clean_outputs.py --cleanup-all          # 删除全部
        """
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录路径 (default: outputs)"
    )
    parser.add_argument(
        "--keep-latest",
        type=int,
        help="保留最新的 N 个实验"
    )
    parser.add_argument(
        "--keep-recent",
        type=int,
        help="保留最近 N 秒内创建的实验 (例如 86400 = 24小时)"
    )
    parser.add_argument(
        "--cleanup-all",
        action="store_true",
        help="删除所有实验输出"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预留运行 - 只显示将发生什么，不实际删除"
    )

    args = parser.parse_args()

    # 列出所有实验
    experiments = list_experiments(args.output_dir)

    if not experiments:
        print("ℹ️  没有找到实验输出")
        return

    print(f"📁 输出目录: {args.output_dir}")
    print(f"📊 发现 {len(experiments)} 个实验\n")

    # 确定待删除列表
    to_delete = []

    if args.cleanup_all:
        to_delete = [exp[0] for exp in experiments]
    elif args.keep_latest:
        to_delete = keep_latest_experiments(experiments, args.keep_latest)
    elif args.keep_recent:
        to_delete = keep_recent_experiments(experiments, args.keep_recent)
    else:
        # 默认：保留最新的5个
        to_delete = keep_latest_experiments(experiments, 5)

    # 显示预览
    display_experiments(experiments, to_delete)

    # 确认删除
    if to_delete and not args.dry_run:
        response = input(f"\n⚠️  确认删除 {len(to_delete)} 个实验? (y/N): ").strip().lower()
        if response != 'y':
            print("❌ 已取消")
            return

    # 执行清理
    cleanup(args.output_dir, to_delete, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
