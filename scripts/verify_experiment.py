"""Verify SHA-256 of an archived server execution result (read only).

Usage: python scripts/verify_experiment.py outputs/experiment_<id>/reports/summary.json
This detects modifications to the archived full result; it does not attest the
scientific correctness of the detector or reproduce a vanished runtime.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

try:
    from scripts.server.execution_provenance import canonical_bytes, sha256_bytes
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.server.execution_provenance import canonical_bytes, sha256_bytes


def verify(summary_path: Path) -> dict:
    summary_path = Path(summary_path).resolve()
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    provenance = summary.get('provenance') or {}
    if provenance.get('authority') != 'server_execution':
        return {'status': 'unverified_legacy', 'reason': 'No server execution provenance'}
    artifact = provenance.get('full_result_artifact')
    if artifact != 'reports/execution_result.json.gz':
        return {'status': 'invalid', 'reason': 'Unrecognized result artifact path'}
    result_path = summary_path.parent / 'execution_result.json.gz'
    if not result_path.is_file():
        return {'status': 'invalid', 'reason': 'Missing full result artifact'}
    try:
        with gzip.open(result_path, 'rb') as handle:
            decoded = json.load(handle)
        actual = sha256_bytes(canonical_bytes(decoded))
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return {'status': 'invalid', 'reason': type(exc).__name__}
    # The input manifest must agree with the server-computed full result.
    # This matters for digest-only snapshots, whose matrix bytes are not saved.
    result_evidence = decoded.get('input_evidence') if isinstance(decoded, dict) else None
    archived_evidence = provenance.get('effective_model_inputs')
    if result_evidence is not None and result_evidence != archived_evidence:
        return {'status': 'mismatch', 'reason': 'Provenance/input evidence differs from full result'}
    expected = provenance.get('result_sha256')
    config_path = summary_path.parent / 'execution_config.json'
    if provenance.get('config_artifact') != 'reports/execution_config.json' or not config_path.is_file():
        return {'status': 'invalid', 'reason': 'Missing execution config artifact'}
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config_matches = (sha256_bytes(canonical_bytes(config)) == provenance.get('config_sha256'))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        config_matches = False
    inputs = provenance.get('effective_model_inputs') or {}
    snapshot_status = inputs.get('snapshot_status', 'not_captured')
    snapshot_matches = None
    if snapshot_status == 'complete':
        if provenance.get('input_snapshot_artifact') != 'reports/execution_inputs.json.gz':
            return {'status': 'invalid', 'reason': 'Missing declared input snapshot artifact'}
        input_path = summary_path.parent / 'execution_inputs.json.gz'
        if not input_path.is_file():
            return {'status': 'invalid', 'reason': 'Missing input snapshot artifact'}
        try:
            from scripts.core.input_evidence import verify_input_snapshot
            with gzip.open(input_path, 'rb') as handle:
                snapshot = json.load(handle)
            snapshot_matches = verify_input_snapshot(snapshot, inputs)
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
            snapshot_matches = False
    elif provenance.get('input_snapshot_artifact'):
        return {'status': 'invalid', 'reason': 'Undeclared input snapshot artifact'}
    consistent = (actual == expected and config_matches and snapshot_matches is not False)
    return {'status': 'verified_archive_digest' if consistent else 'mismatch',
            'sha256': actual, 'expected': expected, 'config_matches': config_matches,
            'input_snapshot_status': snapshot_status, 'input_snapshot_matches': snapshot_matches,
            'note': 'Matching hashes prove local archive integrity only, not an external signature; '
                    'digest-only inputs cannot be reconstructed from this archive.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary_json', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.summary_json), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
