# AAS V2 P3-D Progress — 2026-11-07

## Completed implementation units

| Unit | Deliverable | Verification |
|---|---|---|
| P3-D1 | Unified TaskRuntime lifecycle, cancellation state machine, durable task history, authenticated task-status/task-cancel APIs | 48 focused regression tests passed at Task 1 gate |
| P3-D2 | Algorithm worker process-tree cancellation; training cleanup; atomic transition to non-cancellable model/provenance/archive stages | 61 passed; one known baseline test deselected |
| P3-D3 | Supervised evaluation and score-only inference run as durable asynchronous tasks; task IDs, progress, safe failure diagnostics and cancellation | 85 passed, 1 skipped; two known baseline tests deselected; Node UI regressions passed |
| P3-D4 | Phase 49 batch import mapped to TaskRuntime; cancellation at per-file transaction boundaries; receipt journal preserved | 109 passed, 1 skipped; two known baseline tests deselected |
| P3-D5 | Chunked HTTP upload, disk-backed CSV/GZIP decompression, row-stream parsing into SQLite staging, bounded task summaries, cancellation before target write transaction | 165 passed, 1 known baseline test deselected; 50k-frame stress import passed |

## Current behavior

- Task states: running, cancelling, completed, failed, cancelled, interrupted.
- Confirmed cancellation is idempotent; terminal tasks cannot be cancelled.
- Worker processes are terminated as process trees; concurrency slots are released.
- Training, evaluation and score-only inference cannot be cancelled after the algorithm stage atomically transitions into model/provenance/report finalization.
- Batch imports check cancellation before and after each file transaction. Committed files and per-file receipts remain; files not yet started are skipped.
- Generic CSV/CSV.GZ source imports are staged in 256 KiB chunks, parsed incrementally, and written to the target database in one transaction. Decompressed CSV is limited to 12 MB; upload is limited to 64 MB.
- Service restart marks in-flight TaskRuntime tasks interrupted; no task is automatically replayed.
- Existing /api/train-status remains compatible; /api/task-status, /api/task-history?include_all=1 and /api/task-cancel provide generic task access.

## Known baseline failures

The following two existing tests were already failing on the P3-C import baseline:

1. Phase 41 test expects a private one-time execution_receipt in task result. Do not expose the receipt to satisfy this stale test.
2. V2 algorithm parameter HTTP contract expects algorithm parameter validation before dataset-selection validation. This is an existing validation-order contract mismatch.

The D1-D5 focused regression excludes the Phase 41 stale receipt assertion. The second known validation-order mismatch is tracked separately. The full repository suite and Windows acceptance are not yet declared Stable.

## Remaining P3-D work

- P3-D6: shared UI polling, upload/compute progress, cancellation confirmation, all-task history, training/evaluation cancellation and Phase 49 batch-import TaskRuntime wiring.

Then proceed to P3-E history browsing and P3-F/P3-G result and fault-event visualization, followed by P3-H Windows 10/11 acceptance.

## Git and remote recording

- Local source branch: feat/aas-p3d-long-task-runtime.
- Local P3-D5 commit: 23956732b4c47309844cbc1af688ea84b856d81b.
- Remote repository: clonetimer/AAS, branch feat/aas-p3d-long-task-runtime.
- Remote branch contains design, plan, baseline and progress records plus Task 1–3 patch artifacts and the P3-D5 progress report. It does not yet contain the complete AAS source tree. Do not describe it as a complete cloneable source repository until that source tree has been imported and verified.
