# AAS V2 P3-D5 — Disk-backed CSV/CSV.GZ import

Date: 2026-11-07

## Local source and commit

- Full local source candidate: AAS_V2_P3D_D1-D4_20261107_candidate.zip
- Candidate ZIP SHA-256: d18e777f7877a1c70d58a99a133bf59fe7b2b9e0bb2a5bb15703acbed024e267
- Local import baseline: 966158a0c3107bed73410eacf1d2b30e706f3ede
- P3-D5 local commit: 23956732b4c47309844cbc1af688ea84b856d81b
- Local branch: feat/aas-p3d-long-task-runtime

The source ZIP did not contain .git metadata. The local branch starts from a fresh import of the verified full-source candidate.

## Implementation

1. POST /api/v2/telemetry/import-source reads the HTTP request body in 256 KiB chunks into a server-side staging file while calculating SHA-256; it no longer reads the full upload into a Python bytes object.
2. TaskRuntime-backed applications now create durable csv_import tasks for inspect, preview and commit. The HTTP route returns 202 and a task_id; progress is recorded through upload, parsing, validation and database phases.
3. CSV.GZ is decompressed incrementally to a temporary file with a hard 12 MB expanded-size limit. Corrupt streams, concatenated GZIP members and trailing bytes are rejected.
4. Encoding detection validates the complete disk-backed file incrementally (UTF-8/BOM or GB18030); delimiter detection uses a bounded sample.
5. CSV rows are parsed one at a time into a temporary SQLite staging database. The target V2 database is written in batches in one SQLite transaction.
6. Cancellation is checked during decompression and row staging. Before the target write phase, TaskRuntime atomically changes the task to non-cancellable. If cancellation wins before that boundary, no target source/channel/segment/sample rows are committed.
7. Temporary upload and staging files are cleaned after completion, failure or cancellation. Abandoned upload files older than 24 hours are cleaned when a new import starts.
8. CSV task result persistence is allow-listed and bounded; it excludes request configuration, raw rows, telemetry matrices, import context and raw cell values in quality issues.
9. Existing synchronous compatibility facades remain supported. SQLite imports retain their existing bounded 64 MB path; this implementation specifically streams CSV/CSV.GZ.
10. CSV import failures now produce actionable encoding, compression, size and content diagnostics.

## Verification

Focused D1-D5 regression command:

```bash
python -m pytest -q tests/test_p3d_streaming_source_import.py tests/test_v2_source_file_import_phase33.py tests/test_v2_csv_workspace_upgrade.py tests/test_phase48_batch_streaming.py tests/test_phase49_batch_ui.py tests/test_task_runtime_cancellation.py tests/test_task_algorithm_cancellation.py tests/test_task_async_evaluation.py tests/test_task_history_api.py tests/test_task_diagnostics.py tests/test_phase40_training_reliability.py tests/test_phase41_algorithm_process.py -k 'not test_http_bound_training_route_completes_using_child_process'
```

Result: **165 passed, 1 deselected**. The deselected Phase 41 assertion expects a private one-time execution_receipt in a public task result; that receipt is intentionally not exposed.

Frontend regressions before D6 integration:
- runner_csv_import_ui.cjs: PASS
- runner_csv_format_behavior.cjs: PASS
- runner_score_only_ui.cjs: PASS
- JavaScript syntax checks, Python compileall and git diff --check: PASS

50,000-frame stress experiment:
- 50,000 frames × 4 telemetry columns.
- Uncompressed CSV: 2,220,113 bytes; compressed CSV.GZ: 607,398 bytes.
- Imported samples: 200,000.
- Runtime: 2.61 seconds in the Linux environment.
- Process peak RSS: 112.6 MiB (includes interpreter and test process baseline; not a standalone incremental-memory measurement).

## Remaining release boundary

- P3-D6 must update the production CSV wizard to consume HTTP 202 task IDs, poll task status, show progress and support confirmed cancellation. Until D6 is complete, the production CSV wizard is not compatible with the new asynchronous endpoint.
- Training/evaluation cancellation UI, batch-import TaskRuntime route wiring and cross-page task-history controls are also part of D6. The batch worker already supports optional TaskRuntime integration, but its POST route and UI still need to connect it.
- Windows 10/11 has not been tested. Stable acceptance is not claimed.
- P3-E, P3-F, P3-G and P3-H remain outstanding.

## Source completeness

This repository currently stores progress/design records and reproducible patch artifacts, not the complete application source tree. The local D1-D5 source candidate remains the full application package until a complete source import into this repository is verified.
