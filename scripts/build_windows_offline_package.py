#!/usr/bin/env python3
"""Build a clean Windows offline delivery ZIP from a source tree and a trusted payload seed.

The seed contributes only the Python runtime, pinned wheelhouse, and example
telemetry/labels. Models are intentionally excluded from the default package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Tuple


EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "dist", "build", "outputs", "logs",
    ".cache", "cache", "user_csv_workspaces", "source_csv_gz", "source_labels",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".log", ".tmp", ".bak", ".sqlite", ".sqlite3", ".db",
    ".pkl", ".pickle", ".joblib", ".pt", ".pth", ".onnx", ".whl", ".zip",
}
EXCLUDED_FILENAMES = {".aas-service.json", ".aas-install.json", ".env", ".coverage"}
PAYLOAD_DIRS = (
    Path("程序/Python安装包"),
    Path("程序/Python便携版"),
    Path("程序/运行依赖"),
    Path("数据/遥测"),
    Path("数据/标签"),
)


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> List[Tuple[str, str]]:
    result = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        if not match:
            raise ValueError("离线锁定依赖必须使用精确的 name==version：{}".format(line))
        result.append((normalize_distribution(match.group(1)), match.group(2)))
    if not result:
        raise ValueError("离线依赖锁定文件为空")
    return result


def validate_wheelhouse(lock_file: Path, wheelhouse: Path) -> None:
    required = set(parse_lock(lock_file))
    found = set()
    wheel_files = list(wheelhouse.glob("*.whl"))
    for wheel in wheel_files:
        pieces = wheel.name[:-4].split("-")
        if len(pieces) < 5:
            raise ValueError("wheelhouse 含无效 wheel 文件名：{}".format(wheel.name))
        identity = (normalize_distribution(pieces[0]), pieces[1])
        if identity in found:
            raise ValueError("wheelhouse 含重复包版本的多个 wheel：{}".format(wheel.name))
        found.add(identity)
    missing = sorted(required - found)
    extras = sorted(found - required)
    if missing:
        raise ValueError("wheelhouse 缺少锁定依赖：{}".format(", ".join(
            "{}=={}".format(name, version) for name, version in missing)))
    if extras:
        raise ValueError("wheelhouse 含锁定清单之外的 wheel：{}".format(", ".join(
            "{}=={}".format(name, version) for name, version in extras)))


def source_commit(source_root: Path, explicit: str) -> str:
    if explicit:
        if not re.fullmatch(r"[0-9a-f]{40}", explicit):
            raise ValueError("--source-commit 必须是 40 位小写十六进制 Git SHA")
        return explicit
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(source_root),
            stderr=subprocess.DEVNULL, universal_newlines=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        value = ""
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("源码目录不是 Git 工作树；请通过 --source-commit 指定 40 位提交 SHA")
    return value


def collect_source_files(source_root: Path) -> List[Path]:
    files = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source_root)
        parts = relative.parts
        if any(part in EXCLUDED_DIRS for part in parts):
            continue
        if parts and parts[0] == "models":
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if path.name in EXCLUDED_FILENAMES:
            continue
        if path.name in {".DS_Store", "Thumbs.db"}:
            continue
        if path.name.endswith(("-wal", "-shm")):
            continue
        files.append(relative)
    return sorted(files, key=lambda item: item.as_posix().lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_seed_manifest(seed_root: Path) -> dict:
    """Verify every file in the original offline seed before trusting its payload."""
    manifest_path = seed_root / "校验" / "双坐标文件哈希.json"
    if not manifest_path.is_file():
        raise ValueError("离线种子包缺少原始 SHA-256 清单：校验/双坐标文件哈希.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("离线种子包 SHA-256 清单无法读取：{}".format(exc)) from exc
    if not isinstance(manifest, dict):
        raise ValueError("离线种子包 SHA-256 清单格式无效")
    commit = manifest.get("source_commit")
    entries = manifest.get("entries")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("离线种子包缺少有效的原始源码提交 SHA")
    if not isinstance(entries, dict) or len(entries) < 20:
        raise ValueError("离线种子包 SHA-256 清单条目不足或格式无效")

    root = seed_root.resolve()
    seen = set()
    for relative, metadata in entries.items():
        if (not isinstance(relative, str) or not relative or chr(92) in relative
                or ":" in relative or relative.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("离线种子包清单包含非法路径：{}".format(relative))
        key = relative.casefold()
        if key in seen:
            raise ValueError("离线种子包清单包含重复路径：{}".format(relative))
        seen.add(key)
        parts = PurePosixPath(relative).parts
        candidate = (root.joinpath(*parts)).resolve()
        if os.path.commonpath((str(root), str(candidate))) != str(root):
            raise ValueError("离线种子包清单路径越界：{}".format(relative))
        if not candidate.is_file():
            raise ValueError("离线种子包缺少文件：{}".format(relative))
        if not isinstance(metadata, dict):
            raise ValueError("离线种子包文件元数据无效：{}".format(relative))
        expected_bytes = metadata.get("bytes")
        expected_hash = metadata.get("sha256")
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError("离线种子包文件大小元数据无效：{}".format(relative))
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("离线种子包 SHA-256 元数据无效：{}".format(relative))
        if candidate.stat().st_size != expected_bytes:
            raise ValueError("离线种子包文件大小校验失败：{}".format(relative))
        if sha256_file(candidate) != expected_hash:
            raise ValueError("离线种子包 SHA-256 校验失败：{}".format(relative))
    return manifest


def copy_payload(seed_root: Path, package_root: Path) -> None:
    verify_seed_manifest(seed_root)
    for relative in PAYLOAD_DIRS:
        source = seed_root / relative
        if not source.is_dir():
            raise ValueError("离线种子包缺少目录：{}".format(relative.as_posix()))
        destination = package_root / relative
        shutil.copytree(source, destination)
    targets = package_root / "数据" / "标签" / "targets.zip"
    if not targets.is_file():
        raise ValueError("离线种子包缺少 数据/标签/targets.zip")
    lock = package_root / "程序" / "运行依赖" / "锁定版本.txt"
    wheelhouse = package_root / "程序" / "运行依赖" / "wheelhouse"
    validate_wheelhouse(lock, wheelhouse)
    if not (package_root / "程序" / "Python安装包" / "python-3.8.10-amd64.exe").is_file():
        raise ValueError("离线种子包缺少 CPython 3.8.10 x64 安装程序")
    if not (package_root / "程序" / "Python便携版" / "python-3.8.10-embed-amd64.zip").is_file():
        raise ValueError("离线种子包缺少 CPython 3.8.10 embeddable 运行时")
    if not list((package_root / "数据" / "遥测").glob("*.csv.gz")):
        raise ValueError("离线种子包没有示例遥测 CSV.GZ")


def build_source_archive(source_root: Path, package_root: Path) -> int:
    source_zip = package_root / "程序" / "AAS_源码.zip"
    source_zip.parent.mkdir(parents=True, exist_ok=True)
    source_files = collect_source_files(source_root)
    if not source_files:
        raise ValueError("源码目录为空")
    with zipfile.ZipFile(source_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in source_files:
            archive.write(str(source_root / relative), arcname=(Path("AAS") / relative).as_posix())
    return len(source_files)


def write_manifest(package_root: Path, commit: str, seed_name: str) -> Path:
    entries: Dict[str, Dict[str, object]] = {}
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name == "双坐标文件哈希.json":
            continue
        relative = path.relative_to(package_root).as_posix()
        entries[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    manifest = {
        "product": "AAS Windows Offline",
        "baseline": seed_name,
        "source_archive": "程序/AAS_源码.zip",
        "source_commit": commit,
        "models_included": False,
        "entries": entries,
    }
    target = package_root / "校验" / "双坐标文件哈希.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def write_package_zip(package_root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(package_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(package_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as source, archive.open(info, "w") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


def build_package(source_root: Path, seed_root: Path, output: Path, commit: str, overwrite: bool = False) -> dict:
    source_root = source_root.resolve()
    seed_root = seed_root.resolve()
    output = output.resolve()
    if not source_root.is_dir() or not seed_root.is_dir():
        raise ValueError("源码目录和离线种子包目录必须存在")
    if output.exists() and not overwrite:
        raise FileExistsError("输出文件已存在；如需替换请使用 --overwrite：{}".format(output))
    commit = source_commit(source_root, commit)
    with tempfile.TemporaryDirectory(prefix="aas-offline-build-") as temporary:
        package_root = Path(temporary) / "package"
        package_root.mkdir()
        copy_payload(seed_root, package_root)
        source_count = build_source_archive(source_root, package_root)

        tools = source_root / "deploy" / "windows"
        for name in (
            "Deploy-AAS.ps1", "Start-AAS.ps1", "Stop-AAS.ps1", "Uninstall-AAS.ps1",
            "一键部署.cmd", "Start-AAS.cmd", "Stop-AAS.cmd", "Uninstall-AAS.cmd",
        ):
            source = tools / name
            if not source.is_file():
                raise ValueError("缺少部署工具：{}".format(source))
            shutil.copy2(source, package_root / name)

        readme = tools / "部署说明.md"
        if not readme.is_file():
            raise ValueError("缺少离线包用户部署说明：{}".format(readme))
        shutil.copy2(readme, package_root / "部署说明.md")

        # No experiment models, model registry DB, pickle files, or backup configs
        # are copied into a normal user installation.
        if (package_root / "数据" / "模型").exists():
            shutil.rmtree(package_root / "数据" / "模型")
        write_manifest(package_root, commit, seed_root.name)
        write_package_zip(package_root, output)

    with zipfile.ZipFile(output, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError("生成 ZIP CRC 校验失败：{}".format(bad))
        names = set(archive.namelist())
        forbidden = [name for name in names if
                     name.startswith("数据/模型/") or name.endswith(".bak") or
                     "/__pycache__/" in name or name.endswith(".pyc")]
        if forbidden:
            raise ValueError("交付包包含禁止文件：{}".format(", ".join(forbidden[:10])))
        manifest_data = json.loads(archive.read("校验/双坐标文件哈希.json").decode("utf-8"))
        for relative, metadata in manifest_data["entries"].items():
            info = archive.getinfo(relative)
            if info.file_size != metadata["bytes"]:
                raise ValueError("交付包文件大小与清单不一致：{}".format(relative))
            digest = hashlib.sha256()
            with archive.open(relative, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != metadata["sha256"]:
                raise ValueError("交付包 SHA-256 与清单不一致：{}".format(relative))
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "source_files": source_count,
        "manifest_entries": len(manifest_data["entries"]),
        "source_commit": commit,
        "models_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AAS Windows offline delivery ZIP")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--seed-root", required=True, type=Path,
                        help="解压后的已校验 Windows 离线包目录；仅复用 Python、wheelhouse、示例数据")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = build_package(args.source_root, args.seed_root, args.output,
                           args.source_commit, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
