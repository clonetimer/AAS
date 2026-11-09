"""Verify AAS candidate source ZIP integrity before Windows offline installation.

Usage: python LOCAL_TEST/Verify-UpgradeSourceZip.py candidate.zip
Optionally pass --expected-sha256 HASH copied from the delivery note.
The embedded manifest detects accidental corruption; an independently obtained
expected SHA-256 detects replacement of the whole ZIP including its manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


MANIFEST = 'AAS/UPGRADE_SHA256_MANIFEST.json'


def verify(path: Path, expected_sha256: str | None = None) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f'ZIP 不存在：{path}')
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected_sha256 and sha != expected_sha256.lower().strip():
        raise ValueError('交付 ZIP SHA-256 不匹配（请重新下载）')
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise ValueError('ZIP 内含重复路径')
        for name in members:
            parts = PurePosixPath(name).parts
            if (not name.startswith('AAS/') or name.startswith('/') or
                    '\\' in name or '..' in parts or any(p == '' for p in parts)):
                raise ValueError(f'不安全的 ZIP 路径：{name}')
            if name.startswith(('AAS/.git/', 'AAS/.venv/', 'AAS/models/',
                                'AAS/outputs/')):
                raise ValueError(f'包含运行环境或模型目录：{name}')
        if MANIFEST not in members:
            raise ValueError('manifest 缺失')
        if archive.testzip() is not None:
            raise ValueError('ZIP 内容 CRC 校验失败')
        manifest = json.loads(archive.read(MANIFEST))
        if manifest.get('format') != 'aas-v2-source-manifest-sha256-v1':
            raise ValueError('manifest 格式不支持')
        entries = manifest.get('files')
        if not isinstance(entries, dict) or set(entries) != set(members)-{MANIFEST}:
            raise ValueError('manifest 文件集合与 ZIP 实际文件不匹配')
        for name, digest in entries.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f'manifest 校验失败：{name}')
        return {'file_count': len(entries), 'zip_sha256': sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip_path', type=Path)
    parser.add_argument('--expected-sha256', default=None)
    args = parser.parse_args()
    try:
        result = verify(args.zip_path, args.expected_sha256)
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    print(f"PASS: {result['file_count']} source files verified; ZIP SHA-256: {result['zip_sha256']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
