"""Backfill ``feature_schema`` into already published V2 run-subset models.

R3.8 artifacts were trained before the input layout was pinned, so their
``data_contract`` only carries positional ``feature_columns``. Everything needed
to reconstruct the identity is already inside those artifacts:

* ``v2_run_selection.feature_keys`` - the exact key order the model was fit on
* ``v2_run_manifest.runs[0].semantic_features`` - subsystem/device/variable/unit

No retraining and no database access is required. The recorded order is kept
verbatim (``order_policy='as_trained'``): normalising it would silently transpose
the columns the model actually learned.

Run: python scripts/backfill_feature_schema.py
Run: python scripts/backfill_feature_schema.py --apply
Run: python scripts/backfill_feature_schema.py --apply --models-dir models
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.server.feature_schema import (  # noqa: E402  (needs sys.path first)
    ORDER_POLICY_AS_TRAINED,
    build_feature_schema,
    schema_digest,
    stored_feature_schema,
    verify_stored_feature_schema,
)


def _expected_schema(contract: dict) -> tuple[dict | None, str]:
    """Reconstruct the schema a legacy model was trained under."""
    selection = contract.get('v2_run_selection') or {}
    keys = selection.get('feature_keys')
    if not isinstance(keys, list) or not keys:
        return None, 'no_feature_keys'
    runs = (contract.get('v2_run_manifest') or {}).get('runs') or []
    semantics = runs[0].get('semantic_features') if runs else None
    if not isinstance(semantics, list) or len(semantics) != len(keys):
        # Missing semantics is not fatal: keys + bindings still pin the layout.
        semantics = None
    schema = build_feature_schema(keys, semantics, order_policy=ORDER_POLICY_AS_TRAINED)
    columns = [str(name) for name in (contract.get('feature_columns') or [])]
    if columns and list(schema['bindings']) != columns:
        return None, 'column_binding_mismatch'
    return schema, 'ok'


def _write(model_dir: Path, contract: dict, schema: dict, *, apply: bool) -> str:
    config_path = model_dir / 'config.json'
    raw = config_path.read_text(encoding='utf-8')
    config = json.loads(raw)
    data_contract = config.setdefault('config', {}).setdefault('data_contract', {})
    data_contract['feature_schema'] = schema
    data_contract['schema_sha256'] = schema_digest(schema)
    if not apply:
        return 'planned'
    shutil.copy2(config_path, config_path.with_suffix('.json.bak'))
    temp = config_path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, config_path)
    verify_stored_feature_schema(data_contract)
    return 'applied'


def backfill(models_dir: Path, *, apply: bool) -> dict:
    summary: dict[str, list[str]] = {}
    for config_path in sorted(models_dir.glob('*/config.json')):
        model_dir = config_path.parent
        name = model_dir.name
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            summary.setdefault('unreadable', []).append(f'{name}: {exc}')
            continue
        contract = (config.get('config') or {}).get('data_contract') or {}
        if contract.get('version') != 7 or not contract.get('v2_run_selection'):
            summary.setdefault('not_v2_run_model', []).append(name)
            continue
        existing = stored_feature_schema(contract)
        if existing is not None:
            try:
                verify_stored_feature_schema(contract)
            except ValueError as exc:
                summary.setdefault('existing_invalid', []).append(f'{name}: {exc}')
            else:
                summary.setdefault('already_bound', []).append(name)
            continue
        schema, reason = _expected_schema(contract)
        if schema is None:
            summary.setdefault(reason, []).append(name)
            continue
        status = _write(model_dir, contract, schema, apply=apply)
        summary.setdefault(status, []).append(f'{name} {schema_digest(schema)[:12]}')
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', default=str(ROOT / 'models'))
    parser.add_argument('--apply', action='store_true',
                       help='write changes; without it nothing is modified')
    args = parser.parse_args(argv)

    models_dir = Path(args.models_dir)
    if not models_dir.is_dir():
        print(f'模型目录不存在：{models_dir}')
        return 2
    summary = backfill(models_dir, apply=args.apply)
    print('模式：' + ('写入' if args.apply else '预演（加 --apply 才会写入）'))
    for status in sorted(summary):
        entries = summary[status]
        print(f'{status} ({len(entries)})')
        for entry in entries:
            print(f'  - {entry}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
