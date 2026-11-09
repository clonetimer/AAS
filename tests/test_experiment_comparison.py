"""Input-equivalence and archive-trust regression, including HTTP authorization."""
from __future__ import annotations

import gzip
import json
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pytest

from detectors.mlp_detector import MLPDetector
from scripts import visualization_server as app
from scripts.core.data_loader import DataBundle
from scripts.core.input_evidence import capture_stage, finish_evidence, verify_input_snapshot
from scripts.server.execution_provenance import (canonical_bytes, randomness_evidence,
                                                 sha256_bytes)
from scripts.server.experiment_comparison import (compare_archived_experiments,
                                                  compare_manifests)
from tests.http_test_handler import AuthorizedTestHandler


def _bundle() -> DataBundle:
    rows = [{'timestamp': i, 'value': float(i + 1), 'label': 0}
            for i in range(10)]
    return DataBundle(train_rows=rows[:7], test_rows=rows[7:],
                      train_value_rows=rows[:7], test_value_rows=rows[7:],
                      train_values=[[float(i + 1)] for i in range(7)],
                      test_values=[[float(i + 1)] for i in range(7, 10)],
                      mode='ratio_split', metadata={'alignment': {}})


def _evidence(*, scale=1, different_split=False, value_offset=0):
    bundle = _bundle()
    if different_split:
        bundle.train_value_rows = bundle.train_value_rows[1:]
        bundle.train_values = bundle.train_values[1:]
    if value_offset:
        bundle.train_values = [[v[0] + value_offset] for v in bundle.train_values]
        bundle.test_values = [[v[0] + value_offset] for v in bundle.test_values]
    raw = capture_stage(bundle, 'raw', ['value'])
    bundle.train_values = [[v[0] * scale] for v in bundle.train_values]
    bundle.test_values = [[v[0] * scale] for v in bundle.test_values]
    model = capture_stage(bundle, 'model', ['value'])
    return finish_evidence(raw, model, bundle, ['value'], {'scale': scale})


def _archive(root: Path, experiment_id: str, evidence: dict, snapshot: dict | None,
             *, kind='training', algorithm='mlp_detector', seed=42) -> Path:
    reports = root / 'outputs' / f'experiment_{experiment_id}' / 'reports'
    reports.mkdir(parents=True)
    result = {'metrics': {'accuracy': 1.0}, 'input_evidence': evidence}
    config = {'algorithm': algorithm, 'algorithmParams': {'random_state': seed}}
    summary = {'experiment_id': experiment_id, 'type': kind, 'provenance': {
        'authority': 'server_execution',
        'result_sha256': sha256_bytes(canonical_bytes(result)),
        'config_sha256': sha256_bytes(canonical_bytes(config)),
        'full_result_artifact': 'reports/execution_result.json.gz',
        'config_artifact': 'reports/execution_config.json',
        'effective_model_inputs': evidence,
    }}
    with gzip.open(reports / 'execution_result.json.gz', 'wb') as handle:
        handle.write(canonical_bytes(result))
    (reports / 'execution_config.json').write_bytes(canonical_bytes(config))
    if snapshot is not None:
        with gzip.open(reports / 'execution_inputs.json.gz', 'wb') as handle:
            handle.write(canonical_bytes(snapshot))
        summary['provenance']['input_snapshot_artifact'] = 'reports/execution_inputs.json.gz'
    path = reports / 'summary.json'
    path.write_bytes(canonical_bytes(summary))
    return path


def test_exact_same_model_input_counts_as_input_controlled_comparison():
    a, snap = _evidence()
    b, _ = _evidence()
    assert verify_input_snapshot(snap, a)
    result = compare_manifests(a, b)
    assert result['classification'] == 'identical_model_inputs'
    assert result['comparable_inputs'] is True
    assert result['sample_selection_identical']['train'] is True
    assert result['raw_inputs_identical']['test'] is True


def test_preprocessing_differences_are_not_mislabeled_algorithm_comparison():
    a, _ = _evidence(scale=1)
    b, _ = _evidence(scale=0.5)
    result = compare_manifests(a, b)
    assert result['classification'] == 'same_raw_inputs_different_preprocessing'
    assert result['comparable_inputs'] is False
    assert result['sample_selection_identical']['test'] is True


def test_same_cohort_but_different_readings_and_split_mismatch():
    a, _ = _evidence()
    different_values, _ = _evidence(value_offset=15)
    assert compare_manifests(a, different_values)['classification'] == 'same_samples_different_values'
    different_split, _ = _evidence(different_split=True)
    result = compare_manifests(a, different_split)
    assert result['classification'] == 'different_or_unconfirmed_inputs'
    assert result['sample_selection_identical']['train'] is False


def test_absent_evidence_and_legacy_selection_are_not_declared_equal_without_basis():
    a, _ = _evidence()
    assert compare_manifests(a, {})['classification'] == 'insufficient_evidence'
    old = deepcopy(a)
    del old['sample_selection']
    assert compare_manifests(a, old)['sample_selection_identical']['train'] is None
    assert compare_manifests(a, old)['classification'] == 'identical_model_inputs'


def test_tampered_selection_manifest_is_detected():
    manifest, snapshot = _evidence()
    bad = deepcopy(manifest)
    bad['sample_selection']['test']['sha256'] = '0' * 64
    assert not verify_input_snapshot(snapshot, bad)


def test_existing_archives_are_verified_before_comparing_and_reject_path_traversal():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        a, snap_a = _evidence()
        b, snap_b = _evidence(scale=2)
        left = _archive(root, 'alpha', a, snap_a)
        _archive(root, 'beta', b, snap_b)
        result = compare_archived_experiments(root, 'alpha', 'beta')
        assert result['classification'] == 'same_raw_inputs_different_preprocessing'
        assert result['left']['archive_status'] == 'verified_archive_digest'
        with pytest.raises(ValueError, match='非法字符'):
            compare_archived_experiments(root, '../../etc', 'beta')
        with pytest.raises(ValueError, match='不同'):
            compare_archived_experiments(root, 'alpha', 'alpha')
        summary = json.loads(left.read_text(encoding='utf-8'))
        summary['provenance']['input_snapshot_artifact'] = 'reports/does_not_exist.json.gz'
        left.write_bytes(canonical_bytes(summary))
        assert compare_archived_experiments(root, 'alpha', 'beta')['classification'] == 'unverified_archives'



def test_verified_equal_supervised_evaluations_show_only_comparable_metric_rows():
    """A timing number or private telemetry is not a comparable diagnostic metric."""
    with TemporaryDirectory() as temp:
        root = Path(temp)
        evidence, snapshot = _evidence()
        left = _archive(root, 'metric_a', evidence, snapshot, kind='evaluation')
        right = _archive(root, 'metric_b', evidence, snapshot, kind='evaluation')
        for path, accuracy, f1, latency in ((left, .88, .70, 20), (right, .91, .75, 40)):
            summary = json.loads(path.read_text(encoding='utf-8'))
            summary['evaluation'] = {'evaluation_mode': 'supervised', 'ground_truth_available': True}
            metrics = {'accuracy': accuracy, 'f1': f1, 'elapsed_seconds': latency}
            result = {'metrics': metrics, 'input_evidence': evidence}
            with gzip.open(path.parent / 'execution_result.json.gz', 'wb') as out:
                out.write(canonical_bytes(result))
            summary['provenance']['result_sha256'] = sha256_bytes(canonical_bytes(result))
            path.write_bytes(canonical_bytes(summary))
        comparison = compare_archived_experiments(root, 'metric_a', 'metric_b')
        assert comparison['classification'] == 'identical_model_inputs'
        metric = comparison['metric_comparison']
        assert metric['status'] == 'comparable'
        assert metric['direction'] == 'right_minus_left'
        assert metric['rows'] == [
            {'key': 'accuracy', 'left': .88, 'right': .91, 'delta': pytest.approx(.03)},
            {'key': 'f1', 'left': .70, 'right': .75, 'delta': pytest.approx(.05)},
        ]


def test_different_inputs_or_missing_ground_truth_never_show_metric_rankings():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        a, snapshot = _evidence()
        b, other = _evidence(value_offset=15)
        left = _archive(root, 'compare_a', a, snapshot, kind='evaluation')
        right = _archive(root, 'compare_b', b, other, kind='evaluation')
        for path in (left, right):
            summary = json.loads(path.read_text(encoding='utf-8'))
            summary['evaluation'] = {'evaluation_mode': 'supervised', 'ground_truth_available': True}
            path.write_bytes(canonical_bytes(summary))
        comparison = compare_archived_experiments(root, 'compare_a', 'compare_b')
        assert comparison['metric_comparison']['status'] == 'not_comparable'
        assert comparison['metric_comparison']['rows'] == []

        # Even with identical input hashes and a legacy accuracy field,
        # score-only has no reference labels and cannot join the ranking.
        unlabelled = _archive(root, 'compare_unlabelled', a, snapshot, kind='evaluation')
        summary = json.loads(unlabelled.read_text(encoding='utf-8'))
        summary['evaluation'] = {'evaluation_mode': 'score_only', 'ground_truth_available': False}
        unlabelled.write_bytes(canonical_bytes(summary))
        comparison = compare_archived_experiments(root, 'compare_a', 'compare_unlabelled')
        assert comparison['classification'] == 'identical_model_inputs'
        assert comparison['metric_comparison']['status'] == 'not_comparable'
        assert comparison['metric_comparison']['rows'] == []


def test_mlp_respects_local_seed_and_does_not_change_global_rng():
    config = {'hidden_sizes': [7], 'epochs': 1}
    before = np.random.get_state()
    def initialize(seed):
        detector = MLPDetector({**config, 'random_state': seed})
        detector._initialize_weights(2)  # pylint: disable=protected-access
        return detector.weights[0].copy()
    first, second, changed = initialize(19), initialize(19), initialize(20)
    assert np.array_equal(first, second)
    assert not np.array_equal(first, changed)
    after = np.random.get_state()
    assert all(np.array_equal(x, y) if isinstance(x, np.ndarray) else x == y
               for x, y in zip(before, after))
    assert randomness_evidence('training', {'algorithm': 'mlp_detector'})['effective_seed'] == 42
    assert randomness_evidence('training', {'algorithm': 'mlp_detector',
           'algorithmParams': {'random_state': 19}})['effective_seed'] == 19
    assert randomness_evidence('training', {'algorithm': 'lstm_autoencoder_detector',
           'algorithmParams': {'random_state': 19}})['control'] == 'algorithm_defined'


def test_http_comparison_is_read_only_authenticated_and_guards_archive_paths():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        a, snap = _evidence()
        _archive(root, 'one', a, snap)
        _archive(root, 'two', a, snap)
        server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(app, 'ROOT', root):
                url = f'http://127.0.0.1:{server.server_port}/api/compare-experiments?'
                with urlopen(url + urlencode({'left': 'one', 'right': 'two'}), timeout=5) as response:
                    result = json.load(response)
                assert result['classification'] == 'identical_model_inputs'
                with pytest.raises(HTTPError) as err:
                    urlopen(url + urlencode({'left': '../escape', 'right': 'two'}), timeout=5)
                assert err.value.code == 400
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()


def test_digest_only_archive_manifest_tampering_cannot_fake_identical_inputs():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        evidence, _ = _evidence()
        digest_only = deepcopy(evidence)
        digest_only['snapshot_status'] = 'digest_only_size_limit'
        digest_only.pop('snapshot_sha256', None)
        left = _archive(root, 'left', digest_only, None)
        _archive(root, 'right', digest_only, None)
        assert compare_archived_experiments(root, 'left', 'right')['classification'] == 'identical_model_inputs'
        summary = json.loads(left.read_text(encoding='utf-8'))
        summary['provenance']['effective_model_inputs']['stages']['raw']['train']['sha256'] = '0' * 64
        left.write_bytes(canonical_bytes(summary))
        assert compare_archived_experiments(root, 'left', 'right')['classification'] == 'unverified_archives'


def test_archived_compare_reports_different_algorithm_controls_and_task_kinds():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        evidence, snapshot = _evidence()
        _archive(root, 'alpha', evidence, snapshot, algorithm='mlp_detector', seed=42)
        _archive(root, 'beta', evidence, snapshot,
                 algorithm='isolation_forest_detector', seed=99)
        result = compare_archived_experiments(root, 'alpha', 'beta')
        assert result['classification'] == 'identical_model_inputs'
        assert result['comparable_inputs'] is True
        assert result['repeatability_controls']['same_algorithm'] is False
        assert result['repeatability_controls']['same_algorithm_parameters'] is False
        assert result['repeatability_controls']['same_runtime'] is None

        _archive(root, 'gamma', evidence, snapshot, kind='evaluation')
        comparison = compare_archived_experiments(root, 'alpha', 'gamma')
        assert comparison['classification'] == 'different_task_types'
        assert comparison['comparable_inputs'] is False
        assert comparison['repeatability_controls']['same_algorithm'] is True
