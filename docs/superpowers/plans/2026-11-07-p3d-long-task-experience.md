# P3-D Long Task Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AAS V2 long-running data and algorithm operations observable, cancellable at safe boundaries, and recoverable as durable status records.

**Architecture:** Extend the existing `TaskRuntime` and `AlgorithmProcessService`; keep the current in-process HTTP architecture and SQLite. Reuse Phase 48/49 disk-streaming import and add cooperative cancellation at file transaction boundaries. Move evaluation and score-only inference into the same task lifecycle as training.

**Tech Stack:** Python 3.11+, `threading`, `subprocess`, `psutil`, `sqlite3`, existing `ThreadingHTTPServer`, browser Fetch API, Node contract tests, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-p3d-long-task-experience-design.md`

## Global Constraints

- Do not add Redis, Celery, a separate worker service, or new mandatory online dependencies.
- Preserve Windows 10/11 and offline deployment compatibility.
- Persist only task status projections; never persist raw CSV, telemetry matrices, request configuration, model objects, exception stacks, or one-time execution receipts.
- Do not automatically resume/replay in-flight work after a server restart; mark it `interrupted`.
- Cancellation is explicit and idempotent; terminal tasks cannot be cancelled.
- Do not interrupt a SQLite file transaction or irreversible model/provenance/archive finalization.
- Keep the existing label semantics, source evidence, model contracts, and SQLite schema unchanged.

## Review Focus

- Cancellation racing with normal completion must produce exactly one terminal state; test `test_cancel_racing_completion_keeps_single_terminal_state`.
- Service restart during `cancelling` must restore `interrupted`, not `cancelled` or `running`; test `test_recovery_marks_cancelling_task_interrupted`.
- A cancellation request during model validation/provenance/archive must be rejected; test `test_cancel_rejected_after_algorithm_stage`.
- A batch import cancelled between files must retain committed file receipts and never leave the current file partially committed; test `test_batch_cancel_stops_at_file_transaction_boundary`.
- Oversized or corrupt gzip uploads must leave no staged file or database rows; test `test_stream_import_rejects_gzip_limit_without_partial_write`.

---

### Task 1: Unified task lifecycle and cancel API

**Files:**
- Modify: `scripts/server/task_runtime.py`
- Modify: `scripts/server/application_context.py`
- Modify: `scripts/visualization_server.py`
- Modify: `scripts/server/handler_get.py`
- Modify: `scripts/server/handler_post.py`
- Test: `tests/test_task_runtime_cancellation.py`
- Test: `tests/test_task_history_api.py`

**Interfaces:**
- Produces: `TaskRuntime.create_task(task_id: str, task_type: str, config: dict | None = None) -> None`.
- Produces: `TaskRuntime.request_cancel_task(task_id: str) -> dict | None`.
- Produces: `TaskRuntime.is_cancel_requested(task_id: str) -> bool`.
- Produces: `TaskRuntime.set_task_cancellable(task_id: str, cancellable: bool, reason: str | None = None) -> None`.
- Produces: `TaskRuntime.list_public_task_statuses(limit: int = 50, include_all: bool = False)` and matching `count_public_task_statuses(include_all: bool = False)`.
- Existing `create_training_task` remains a compatibility wrapper.

- [ ] **Step 1: Write failing tests** for accepted/duplicate cancellation, terminal-task rejection, non-cancellable-stage rejection, restart recovery of `cancelling`, public history with `include_all`, and race-safe single terminal state.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_task_runtime_cancellation.py tests/test_task_history_api.py`; expected: new cancellation API/state tests fail because the interface is absent.
- [ ] **Step 3: Implement** the in-memory cancellation event registry, safe status projection, terminal `cancelled` state, and restart conversion of `running`/`cancelling` to `interrupted`.
- [ ] **Step 4: Add** authenticated `GET /api/task-status?task_id=...` (retain `/api/train-status` alias), `GET /api/task-history?include_all=1`, and confirmed `POST /api/task-cancel`.
- [ ] **Step 5: Run** the focused tests and `python -m pytest -q tests/test_task_runtime_cancellation.py tests/test_task_history_api.py tests/test_task_diagnostics.py`; expected: all selected tests pass.
- [ ] **Step 6: Commit** `feat: add unified task cancellation lifecycle`.

### Task 2: Algorithm worker cancellation

**Files:**
- Modify: `scripts/server/algorithm_process.py`
- Modify: `scripts/server/telemetry_v2_routes.py`
- Modify: `scripts/server/application_context.py`
- Test: `tests/test_phase41_algorithm_process.py`
- Test: `tests/test_task_algorithm_cancellation.py`
- Test: `tests/test_task_runtime_cancellation.py`

**Interfaces:**
- Consumes: Task 1 `is_cancel_requested` and `set_task_cancellable`.
- Produces: `AlgorithmCancelledError` and `cancel_requested: Callable[[], bool] | None` parameters on `run_training`, `run_evaluation`, and `run_score_only`.

- [ ] **Step 1: Write failing tests** proving a worker process is terminated when the cancellation callback turns true, the semaphore is released, and timeout/error behavior remains unchanged.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_phase41_algorithm_process.py`; expected: cancellation-specific tests fail because supervisor methods do not accept a cancellation callback.
- [ ] **Step 3: Implement** cancellation polling in `_run`; raise `AlgorithmCancelledError`; terminate the process tree using the existing POSIX process group / Windows `taskkill /T` strategy.
- [ ] **Step 4: Wire** training to pass the task runtime cancellation callback and disable cancellation before model validation/provenance/report archive.
- [ ] **Step 5: Run** `python -m pytest -q tests/test_phase41_algorithm_process.py tests/test_phase40_training_reliability.py tests/test_task_runtime_cancellation.py`; expected: all selected tests pass.
- [ ] **Step 6: Commit** `feat: stop algorithm worker trees on task cancellation`.

### Task 3: Asynchronous evaluation and score-only inference

**Files:**
- Modify: `scripts/server/telemetry_v2_routes.py`
- Modify: `scripts/server/task_runtime.py` (only if required by Task 1 interfaces)
- Test: `tests/test_task_async_evaluation.py`
- Test: `tests/test_task_diagnostics.py`

**Interfaces:**
- Consumes: Task 1 `create_task`, `complete_task`, `update_task_progress`, `get_public_task_status`.
- Consumes: Task 2 `AlgorithmCancelledError` and supervisor cancellation callback.
- Produces: evaluation and inference POST routes return `{status: "accepted", task_id: string}` and complete through the task runtime.

- [ ] **Step 1: Write failing tests** for accepted task IDs, persisted completion/failure, progress-stage transitions, and cancellation without report/provenance creation.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_task_async_evaluation.py tests/test_task_diagnostics.py`; expected: async-route tests fail because evaluation/inference still return synchronous results.
- [ ] **Step 3: Implement** a shared background-task launcher for evaluation and inference; keep validation errors as structured task failures with durable IDs.
- [ ] **Step 4: Add** stage progress for validation, data loading, algorithm execution, provenance capture, and report archive; make finalization non-cancellable.
- [ ] **Step 5: Run** `python -m pytest -q tests/test_task_async_evaluation.py tests/test_task_diagnostics.py tests/test_v2_score_only_upgrade.py tests/test_v2_training_evaluation_regression_20261020.py`; expected: all selected tests pass or documented baseline failures remain unchanged.
- [ ] **Step 6: Commit** `feat: run evaluation and inference as durable tasks`.

### Task 4: Batch import cooperative cancellation

**Files:**
- Modify: `scripts/server/telemetry_v2_batch_tasks.py`
- Modify: `scripts/server/task_diagnostics.py`
- Modify: `scripts/server/handler_post.py`
- Test: `tests/test_phase49_batch_ui.py`
- Test: `tests/test_task_batch_cancellation.py`

**Interfaces:**
- Consumes: Task 1 `create_task`, `update_task_progress`, `request_cancel_task`, `is_cancel_requested`, and `complete_task`.
- Produces: common `TaskRuntime` lifecycle for `batch_import`; Phase 49 per-file receipt journal remains the detailed import audit record, with the same task ID and terminal status mirrored into `TaskRuntime`.

- [ ] **Step 1: Write failing tests** for cancellation before the first file, between files, repeated cancellation, and cancellation during an active file transaction.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_task_batch_cancellation.py tests/test_phase49_batch_ui.py`; expected: cancellation-specific tests fail because Phase 49 has no cancellation endpoint/state.
- [ ] **Step 3: Implement** a TaskRuntime cancellation callback; check it before starting each file and after the current file transaction completes; never interrupt `import_file` mid-transaction.
- [ ] **Step 4: Ensure** restart converts `running`/`cancelling` to `interrupted`; retry creates a new TaskRuntime task ID and reuses immutable plan/receipt idempotency.
- [ ] **Step 5: Run** the focused batch tests; expected: committed files remain intact, the in-flight file is atomic, and remaining files are not started after cancellation.
- [ ] **Step 6: Commit** `feat: cancel batch imports at file boundaries`.

### Task 5: Disk-backed CSV/CSV.GZ upload and streaming import

**Files:**
- Modify: `scripts/server/handler_post.py`
- Modify: `scripts/server/telemetry_v2_source_import.py`
- Modify: `scripts/server/v2_csv_workspace.py` only where current upload flow materializes the complete file/row list.
- Test: `tests/test_v2_source_file_import_phase33.py`
- Test: `tests/test_v2_csv_workspace_upgrade.py`
- Test: `tests/test_task_stream_import.py`

**Interfaces:**
- Consumes: Task 1 task creation/progress/cancellation.
- Produces: bounded disk-staged upload handling; stream parser that accepts a seekable file path/stream and checks cancellation between chunks/rows.

- [ ] **Step 1: Write failing tests** for chunked request-body staging, gzip expansion limit, cancellation cleanup, corrupt gzip, and no partial database write.
- [ ] **Step 2: Run** the focused import tests; expected: new stream/cancellation tests fail because the existing routes read the entire request into bytes.
- [ ] **Step 3: Implement** bounded disk staging and streaming CSV/GZIP parsing; do not keep both compressed bytes and full decompressed text in memory.
- [ ] **Step 4: Preserve** format confirmation, source identity digest, unit/column mapping, label semantics, and atomic SQLite commit behavior.
- [ ] **Step 5: Run** `python -m pytest -q tests/test_task_stream_import.py tests/test_v2_source_file_import_phase33.py tests/test_v2_csv_workspace_upgrade.py`; expected: all selected tests pass.
- [ ] **Step 6: Commit** `feat: stream CSV imports through bounded disk staging`.

### Task 6: Cross-page task progress and cancellation UI

**Files:**
- Modify: `web/visualization/runner-v2.js`
- Modify: `web/visualization/runner.html`
- Modify: `web/visualization/reports.js`
- Modify: `web/visualization/styles.css` or `web/visualization/runner-v2.css`
- Test: `tests/task_failure_recovery_ui.cjs`
- Test: `tests/runner_score_only_ui.cjs`
- Test: `tests/task_progress_ui.cjs`

**Interfaces:**
- Consumes: Task 1–5 status API fields and cancellation endpoint.
- Produces: one shared browser polling/cancel flow for training, evaluation, inference and import task pages.

- [ ] **Step 1: Write failing Node contract tests** for status labels, progress updates, cancel confirmation, rejected cancellation, completed/cancelled/interrupted states, and history `include_all` mode.
- [ ] **Step 2: Run** `node tests/task_progress_ui.cjs`; expected: missing shared polling/cancel UI assertions fail.
- [ ] **Step 3: Implement** task polling through `/api/task-status`, cancellation through `/api/task-cancel`, and display of queued/running/cancelling/completed/failed/cancelled/interrupted.
- [ ] **Step 4: Update** evaluation/inference to wait for accepted tasks and show the same progress and terminal states as training.
- [ ] **Step 5: Run** `node tests/task_progress_ui.cjs`, `node tests/task_failure_recovery_ui.cjs`, `node tests/runner_score_only_ui.cjs`, and JavaScript syntax checks; expected: all selected contracts pass.
- [ ] **Step 6: Commit** `feat: expose long task progress and cancellation in UI`.

---

## Final verification

- Run all P3-D focused Python and Node tests.
- Run `python -m pytest -q` with an extended timeout; record every pre-existing failure separately from regressions.
- Run `LOCAL_TEST/Run-LocalTests.ps1 -Mode smoke` on Windows after user-side execution; do not claim Windows acceptance from Linux.
- Run source ZIP CRC and SHA-256 manifest checks.
- Produce a P3-D candidate ZIP and release notes; upload text-based design, plan, patch, test summary, and checksums to `clonetimer/AAS`.
