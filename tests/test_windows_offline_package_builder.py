from pathlib import Path
import hashlib
import json
import zipfile

import pytest

from scripts.build_windows_offline_package import (
    build_package,
    normalize_distribution,
    parse_lock,
    validate_wheelhouse,
    verify_seed_manifest,
)


def _wheelhouse(root: Path):
    lock = root / "程序" / "运行依赖" / "锁定版本.txt"
    wheelhouse = root / "程序" / "运行依赖" / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    lock.write_text("numpy==1.24.4\nPyYAML==6.0.3\n", encoding="utf-8")
    (wheelhouse / "numpy-1.24.4-cp38-cp38-win_amd64.whl").write_bytes(b"wheel")
    (wheelhouse / "PyYAML-6.0.3-cp38-cp38-win_amd64.whl").write_bytes(b"wheel")
    return lock, wheelhouse



def _write_seed_manifest(root: Path):
    entries = {}
    manifest_dir = root / "校验"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            payload = path.read_bytes()
            entries[relative] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    manifest = {"source_commit": "a" * 40, "entries": entries}
    (manifest_dir / "双坐标文件哈希.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

def test_normalizes_python_distribution_names():
    assert normalize_distribution("python_dateutil") == "python-dateutil"


def test_lock_and_wheelhouse_must_match_exactly(tmp_path):
    lock, wheelhouse = _wheelhouse(tmp_path)
    assert len(parse_lock(lock)) == 2
    validate_wheelhouse(lock, wheelhouse)
    (wheelhouse / "unused-1.0-py3-none-any.whl").write_bytes(b"extra")
    with pytest.raises(ValueError, match="锁定清单之外"):
        validate_wheelhouse(lock, wheelhouse)


def test_builder_excludes_models_and_backup_assets(tmp_path):
    source = tmp_path / "source"
    seed = tmp_path / "seed"
    output = tmp_path / "out.zip"
    source.mkdir()
    seed.mkdir()
    (source / "README.md").write_text("AAS", encoding="utf-8")
    tools = source / "deploy" / "windows"
    tools.mkdir(parents=True)
    for name in (
        "Deploy-AAS.ps1", "Start-AAS.ps1", "Stop-AAS.ps1", "Uninstall-AAS.ps1",
        "一键部署.cmd", "Start-AAS.cmd", "Stop-AAS.cmd", "Uninstall-AAS.cmd", "部署说明.md",
    ):
        (tools / name).write_text("stub", encoding="utf-8")
    for relative in (
        "程序/Python安装包", "程序/Python便携版", "程序/运行依赖", "数据/遥测", "数据/标签",
    ):
        (seed / relative).mkdir(parents=True)
    (seed / "程序/Python安装包/python-3.8.10-amd64.exe").write_bytes(b"installer")
    (seed / "程序/Python便携版/python-3.8.10-embed-amd64.zip").write_bytes(b"runtime")
    lock, wheelhouse = _wheelhouse(seed)
    (seed / "数据/遥测/sample.csv.gz").write_bytes(b"sample")
    (seed / "数据/标签/targets.zip").write_bytes(b"labels")
    (seed / "数据/模型").mkdir(parents=True)
    (seed / "数据/模型/model.pkl").write_bytes(b"must not ship")
    _write_seed_manifest(seed)
    result = build_package(source, seed, output, "1" * 40)
    assert result["models_included"] is False
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert not any(name.startswith("数据/模型/") for name in names)
        assert not any(name.endswith(".pkl") or name.endswith(".bak") for name in names)
        assert "程序/AAS_源码.zip" in names
        assert "校验/双坐标文件哈希.json" in names


def test_wheelhouse_rejects_malformed_wheel_filename(tmp_path):
    lock, wheelhouse = _wheelhouse(tmp_path)
    (wheelhouse / "not-a-valid-wheel.whl").write_bytes(b"bad")
    with pytest.raises(ValueError, match="无效 wheel 文件名"):
        validate_wheelhouse(lock, wheelhouse)


def test_wheelhouse_rejects_duplicate_distribution_version(tmp_path):
    lock, wheelhouse = _wheelhouse(tmp_path)
    (wheelhouse / "numpy-1.24.4-cp39-cp39-win_amd64.whl").write_bytes(b"duplicate")
    with pytest.raises(ValueError, match="重复包版本"):
        validate_wheelhouse(lock, wheelhouse)


def test_seed_manifest_rejects_tampered_payload(tmp_path):
    seed = tmp_path / "seed"
    payload = seed / "数据" / "遥测" / "sample.csv.gz"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"trusted sample")
    _write_seed_manifest(seed)
    payload.write_bytes(b"changed sample")
    with pytest.raises(ValueError, match="SHA-256 校验失败"):
        verify_seed_manifest(seed)


def test_seed_manifest_rejects_path_traversal(tmp_path):
    seed = tmp_path / "seed"
    manifest_dir = seed / "校验"
    manifest_dir.mkdir(parents=True)
    manifest = {
        "source_commit": "a" * 40,
        "entries": {"../outside.txt": {"sha256": "0" * 64, "bytes": 0}},
    }
    (manifest_dir / "双坐标文件哈希.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="非法路径"):
        verify_seed_manifest(seed)
