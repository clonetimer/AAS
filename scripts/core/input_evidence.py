"""Evidence for exactly the aligned and scaled matrices supplied to detectors.

This module records *observed* model inputs, not a reconstruction from source
SQL rows.  A compact archive is optional; full-stream digests are always
computed even if the complete evidence would be too large to persist.
"""
from __future__ import annotations

import hashlib
from typing import Any

from scripts.server.execution_provenance import canonical_bytes, sha256_bytes

SNAPSHOT_MAX_ROWS = 20_000
SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024


def capture_stage(bundle: Any, stage: str, feature_columns: list[str],
                  *, snapshot_rows_limit: int = SNAPSHOT_MAX_ROWS,
                  snapshot_bytes_limit: int = SNAPSHOT_MAX_BYTES) -> tuple[dict, dict | None]:
    """Digest both ordered splits and, when small, retain all effective rows.

    Stage ``raw`` is recorded after filtering, alignment and train/test split.
    Stage ``model`` is recorded after scaling, immediately before detector use.
    Only rows that made it into train_values/test_values are included.
    """
    manifest: dict[str, Any] = {'stage': stage, 'splits': {}}
    snapshot: dict[str, Any] = {}
    selection_digests: dict[str, dict[str, Any]] = {}
    total_rows = len(bundle.train_values) + len(bundle.test_values)
    can_store = total_rows <= snapshot_rows_limit
    approx_bytes = 0
    for split in ('train', 'test'):
        values = getattr(bundle, f'{split}_values')
        source_rows = getattr(bundle, f'{split}_value_rows')
        if len(values) != len(source_rows):
            raise ValueError(f'{split} samples and source rows are misaligned')
        digest = hashlib.sha256()
        selection_digest = hashlib.sha256()
        selection_digest.update(canonical_bytes({'schema': 1, 'split': split,
                                                  'selection': 'timestamps_labels_lineage'}) + b'\n')
        digest.update(canonical_bytes({'schema': 1, 'stage': stage,
                                      'split': split, 'features': feature_columns}) + b'\n')
        stored = []
        for value, row in zip(values, source_rows):
            vector = [float(x) for x in value] if isinstance(value, (list, tuple)) else [float(value)]
            if len(vector) != len(feature_columns):
                raise ValueError(f'{split} input dimension does not match feature columns')
            record = {'timestamp': row.get('timestamp'), 'label': row.get('label'), 'values': vector}
            # Preserve the actual selected sensor moments and individual labels
            # for multi-rate fusion; the nominal grid timestamp is not enough
            # to explain which original observations were used.
            lineage = {key: row[key] for key in sorted(row)
                       if key.startswith(('source_timestamp_', 'source_label_'))}
            if lineage:
                record['source_observations'] = lineage
            encoded = canonical_bytes(record)
            digest.update(encoded + b'\n')
            selected = {'timestamp': record['timestamp'], 'label': record['label']}
            if lineage:
                selected['source_observations'] = lineage
            selection_digest.update(canonical_bytes(selected) + b'\n')
            if can_store:
                approx_bytes += len(encoded) + 1
                if approx_bytes > snapshot_bytes_limit:
                    can_store = False
                    snapshot.clear()
                    stored.clear()
                else:
                    stored.append(record)
        manifest['splits'][split] = {'rows': len(values), 'sha256': digest.hexdigest()}
        selection_digests[split] = {'rows': len(values), 'sha256': selection_digest.hexdigest()}
        if can_store:
            snapshot[split] = stored
    manifest['snapshot_status'] = 'complete' if can_store else 'digest_only_size_limit'
    manifest['sample_selection'] = selection_digests
    return manifest, snapshot if can_store else None


def finish_evidence(raw: tuple[dict, dict | None],
                    model: tuple[dict, dict | None], bundle: Any,
                    features: list[str], scaling: dict[str, Any]) -> tuple[dict, dict | None]:
    """Combine phases while preserving the real alignment and scaling settings."""
    raw_meta, raw_snapshot = raw
    model_meta, model_snapshot = model
    # A large dataset never produces a misleading partial archive.
    complete = raw_snapshot is not None and model_snapshot is not None
    alignment = bundle.metadata.get('alignment') or {}
    manifest = {
        'format_version': 1,
        'evidence_scope': 'post_filter_post_alignment_split_and_detector_input',
        'feature_columns': list(features),
        'split_mode': bundle.mode,
        'alignment_sha256': sha256_bytes(canonical_bytes(alignment)),
        'scaling_sha256': sha256_bytes(canonical_bytes(scaling)),
        'stages': {'raw': raw_meta['splits'], 'model': model_meta['splits']},
        'sample_selection': raw_meta['sample_selection'],
        'snapshot_status': 'complete' if complete else 'digest_only_size_limit',
        'limits': {'max_rows_per_stage': SNAPSHOT_MAX_ROWS, 'max_bytes_per_stage': SNAPSHOT_MAX_BYTES},
    }
    if not complete:
        return manifest, None
    snapshot = {
        'format_version': 1, 'feature_columns': list(features),
        'split_mode': bundle.mode, 'alignment': alignment, 'scaling': scaling,
        'stages': {'raw': raw_snapshot, 'model': model_snapshot},
    }
    encoded = canonical_bytes(snapshot)
    if len(encoded) > 2 * SNAPSHOT_MAX_BYTES + 1024 * 1024:
        manifest['snapshot_status'] = 'digest_only_size_limit'
        return manifest, None
    manifest['snapshot_sha256'] = sha256_bytes(encoded)
    return manifest, snapshot


def verify_input_snapshot(snapshot: dict, manifest: dict) -> bool:
    """Verify all four content digests, the feature order and the archive hash."""
    if manifest.get('snapshot_status') != 'complete':
        return False
    if snapshot.get('feature_columns') != manifest.get('feature_columns'):
        return False
    if sha256_bytes(canonical_bytes(snapshot)) != manifest.get('snapshot_sha256'):
        return False
    if sha256_bytes(canonical_bytes(snapshot.get('alignment'))) != manifest.get('alignment_sha256'):
        return False
    if sha256_bytes(canonical_bytes(snapshot.get('scaling'))) != manifest.get('scaling_sha256'):
        return False
    # A separate selection fingerprint permits comparison of sample membership
    # without confusing it with input scaling. Legacy manifests lack this key.
    for split in ('train', 'test'):
        expected_selection = (manifest.get('sample_selection') or {}).get(split)
        if expected_selection is not None:
            records = snapshot.get('stages', {}).get('raw', {}).get(split, [])
            select_hash = hashlib.sha256()
            select_hash.update(canonical_bytes({'schema': 1, 'split': split,
                                                'selection': 'timestamps_labels_lineage'}) + b'\n')
            for record in records:
                selection = {key: record[key] for key in ('timestamp', 'label')}
                if 'source_observations' in record:
                    selection['source_observations'] = record['source_observations']
                select_hash.update(canonical_bytes(selection) + b'\n')
            if expected_selection != {'rows': len(records), 'sha256': select_hash.hexdigest()}:
                return False
    for stage in ('raw', 'model'):
        for split in ('train', 'test'):
            rows = snapshot.get('stages', {}).get(stage, {}).get(split)
            entry = manifest.get('stages', {}).get(stage, {}).get(split, {})
            if not isinstance(rows, list) or entry.get('rows') != len(rows):
                return False
            digest = hashlib.sha256()
            digest.update(canonical_bytes({'schema': 1, 'stage': stage,
                                          'split': split,
                                          'features': manifest['feature_columns']}) + b'\n')
            for record in rows:
                digest.update(canonical_bytes(record) + b'\n')
            if entry.get('sha256') != digest.hexdigest():
                return False
    return True
