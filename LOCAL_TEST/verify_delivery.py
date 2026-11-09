"""Read-only SHA-256 verification of the Phase 53 R7 delivered source files.

Checks the bundled delivery manifest without modifying any file.  Added
LOCAL_TEST files are deliberately separate from the original signed set.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'DELIVERY_SHA256_MANIFEST.txt'

def main() -> int:
    if not MANIFEST.exists():
        print('FAIL: delivery manifest is absent')
        return 2
    count = 0
    for line in MANIFEST.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        digest, delimiter, name = line.partition('  ')
        if not delimiter or len(digest) != 64 or not name.startswith('AAS/'):
            print(f'FAIL: invalid manifest entry near line {count+1}')
            return 2
        relative = Path(name[4:])
        if relative.is_absolute() or '..' in relative.parts:
            print(f'FAIL: unsafe manifest path near line {count+1}')
            return 2
        path = ROOT / relative
        if not path.is_file():
            print(f'FAIL: missing {relative}')
            return 2
        with path.open('rb') as f:
            ctx = hashlib.sha256()
            for part in iter(lambda: f.read(1024 * 1024), b''):
                ctx.update(part)
            h = ctx.hexdigest()
        if h != digest:
            print(f'FAIL: unexpected SHA-256 for {relative}')
            return 2
        count += 1
    print(f'PASS: {count} Phase 53 R7 delivered source files match SHA-256 manifest.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
