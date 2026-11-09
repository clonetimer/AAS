# AAS V2 P3-D Baseline — 2026-11-07

## Source baseline

- Input archive: `AAS_V2_P3C_TaskDiagnostics_20261106_candidate_v3(2).zip`
- Archive SHA-256: `b302f60121c31db8cc71eca458ae3cf6b835ceb81ca58b32589f4f53345a935a`
- ZIP entries: 603; CRC check: passed.
- Imported local Git baseline commit: `271e111a6e86e1715618e648c5f3fda37f185976`
- Feature branch: `feat/aas-p3d-long-task-runtime`
- Source archive did not contain `.git`; this commit is a new local import baseline, not a recovered upstream commit.

## Baseline tests

Command:

```bash
python -m pytest -q tests/test_task_diagnostics.py tests/test_task_history_api.py tests/test_phase41_algorithm_process.py tests/test_phase48_batch_streaming.py tests/test_phase49_batch_ui.py tests/test_phase40_training_reliability.py tests/test_v2_training_evaluation_regression_20261020.py
```

Result: **65 passed, 2 failed, 1 skipped**.

Known baseline failures:

1. `tests/test_phase41_algorithm_process.py::test_http_bound_training_route_completes_using_child_process` expects an `execution_receipt` field in the in-memory task result. The current safe task projection intentionally does not expose or persist one-time execution receipts. This stale test contract must not be “fixed” by exposing the receipt.
2. `tests/test_v2_training_evaluation_regression_20261020.py::test_v2_algorithm_parameter_http_contract` expects an invalid algorithm parameter to be reported before dataset selection validation. The current route rejects the selection first and returns a generic selection diagnostic. This is an existing validation-order issue, not a P3-D regression.

Frontend baseline:
- `node tests/task_failure_recovery_ui.cjs`: PASS.
- `node tests/runner_score_only_ui.cjs`: PASS.
- `node --check web/visualization/runner-v2.js`: PASS.
- `node --check web/visualization/reports.js`: PASS.

## Architecture observations

- Training already runs in a background thread and delegates algorithm work to `AlgorithmProcessService`.
- `AlgorithmProcessService` already terminates POSIX process groups and Windows process trees on timeout/error, but has no user cancellation callback.
- Evaluation and score-only inference currently run synchronously in the HTTP request.
- Phase 48 already streams local CSV/CSV.GZ files into a temporary SQLite staging database.
- Phase 49 already provides a server-local batch import UI and durable per-file receipts, but it has no cancellation; its task journal is separate from `TaskRuntime`.
- Generic browser source import currently receives the full request body as bytes and performs bounded whole-buffer CSV/GZIP decoding.

## Release boundary

The imported candidate is the only verified full-source baseline. The remote repository was created separately and initially contained only README.md; the full source archive is not yet in the remote tree. P3-D changes will be kept as local Git commits, reproducible source ZIPs, and text patches/test reports that can be uploaded through the available GitHub text-file interface. No claim of a complete remote source push is made until the repository tree is actually populated and verified.
