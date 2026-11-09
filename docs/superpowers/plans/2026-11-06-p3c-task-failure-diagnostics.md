# P3-C Task Failure Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain V2 training/evaluation failures by stage and expose safe recovery navigation and recent failed-task history.

**Architecture:** Add a shared diagnostic classifier and persist only a bounded, sanitized task-status projection. Keep training asynchronous and evaluation/inference synchronous for this slice; return the same structured diagnostic shape from synchronous failures. The UI displays next actions and links back to the relevant workflow without replaying saved requests.

**Tech Stack:** Python 3.11+, stdlib dataclasses/json/sqlite3/errno, existing HTTP handlers, browser JavaScript, pytest, Node.js.

**Spec:** `docs/superpowers/specs/2026-11-06-p3c-task-failure-diagnostics-design.md`

## Global Constraints

- Never persist request configuration, CSV rows, telemetry matrices, model objects, access tokens, execution receipts, or exception stack traces.
- A restarted task is `interrupted`; never claim it resumed.
- A retry is a new user-confirmed submission with a new task/run ID.
- Keep original experiment archives immutable and never fabricate a report after archive failure.
- Keep legacy `/api/train-model` and `/api/evaluate-model` retired.
- Preserve current V2 model feature schema, source-isolation, label and metric contracts.

## Review Focus

1. SQLite busy/locked vs corrupt DB: distinguish transient retry from data repair.
2. Disk full / permission denied: no partial success; user receives a safe filesystem action.
3. Service restart: status is interrupted, and no raw request configuration is exposed.
4. Archive failure after successful computation: distinguish from model execution failure and preserve model artifact.
5. Malicious or malformed persisted task JSON: ignore unsafe state and render all messages via textContent.

---

### Task 1: Shared failure taxonomy and safe task projection

**Files:**
- Create: `scripts/server/task_diagnostics.py`
- Modify: `scripts/server/task_runtime.py`
- Test: `tests/test_task_diagnostics.py`
- Test: `tests/test_phase40_training_reliability.py`

**Interfaces:**
- Produces `diagnose_failure(error, stage, task_type='training') -> dict`.
- Produces `TaskRuntime.get_public_task_status(task_id) -> dict | None` and `TaskRuntime.list_public_task_statuses(limit=50) -> list[dict]`.
- `update_task_progress(task_id, progress, message=None, stage=None)` and `complete_task(task_id, result, error=None, failure=None)` retain old positional compatibility.

- [ ] Write tests for stable classification of ENOSPC, PermissionError, SQLite locked/corrupt, AlgorithmTimeoutError, missing input and generic stage failures.
- [ ] Write tests that task state persists stage/diagnostic but not secret config, absolute source paths, raw exception details or receipts.
- [ ] Verify tests fail for missing diagnostic fields and unsafe projection.
- [ ] Implement bounded diagnostic classifier and public task projection.
- [ ] Persist only sanitized stage/diagnostic fields; restore interrupted status with a server-restart diagnostic.
- [ ] Run `python -m pytest -q tests/test_task_diagnostics.py tests/test_phase40_training_reliability.py`.
- [ ] Commit `feat: add structured task failure diagnostics`.

### Task 2: Training stage tracking, archive attention and task history API

**Files:**
- Modify: `scripts/server/telemetry_v2_routes.py`
- Modify: `scripts/server/handler_get.py`
- Modify: `scripts/server/application_context.py`
- Test: `tests/test_v2_auto_archive_upgrade.py`
- Test: `tests/test_task_history_api.py`

**Interfaces:**
- `/api/train-status?task_id=...` returns public task status.
- `GET /api/task-history?limit=50` returns `{items: [...], total: number}` with failed/interrupted/archive-attention tasks.
- Training stage updates use the exact stage keys from the design spec.

- [ ] Add tests for stage labels at each training boundary, including worker-start failure.
- [ ] Add tests for completed training with archive failure being `completed` plus an archive diagnostic, not `failed`.
- [ ] Add live HTTP tests for sanitized single-task status and bounded task history.
- [ ] Implement stage updates and failure diagnostic persistence.
- [ ] Expose public status methods through the bound application context; never return the raw task dictionary.
- [ ] Run focused runtime/archive/API tests.
- [ ] Commit `feat: expose safe task history and recovery status`.

### Task 3: Structured diagnostics for synchronous evaluation and score-only inference

**Files:**
- Modify: `scripts/server/telemetry_v2_routes.py`
- Test: `tests/test_task_diagnostics.py`
- Test: `tests/test_v2_score_only_upgrade.py`
- Test: `tests/test_v2_auto_archive_upgrade.py`

**Interfaces:**
- `TaskOperationError(stage, cause)` preserves the original exception internally.
- Error response is `{error: <safe message>, failure: <diagnostic>}`.
- Successful but unarchived result includes an `archive_attention` diagnostic.

- [ ] Add tests for evaluation input mismatch, algorithm timeout, score-only model incompatibility and archive failure.
- [ ] Verify response messages do not include absolute paths or stack traces.
- [ ] Implement stage-wrapped failures and structured error responses without changing success result contracts.
- [ ] Run focused evaluation, score-only and archive tests.
- [ ] Commit `feat: explain evaluation and inference failures`.

### Task 4: Training/evaluation recovery UI and report task history

**Files:**
- Modify: `web/visualization/runner-v2.js`
- Modify: `web/visualization/runner.html`
- Modify: `web/visualization/reports.js`
- Modify: `web/visualization/reports.html`
- Modify: `web/visualization/runner-v2.css`
- Create: `tests/task_failure_recovery_ui.cjs`

**Interfaces:**
- `window.AASTaskDiagnostics.render(target, diagnostic)` safely renders stage, title, message, action and navigation.
- Reports page loads `/api/task-history` and displays failed/interrupted/archive-attention items.
- Recovery links navigate to `runner.html`; no automatic resubmission.

- [ ] Add DOM tests for failed/interrupted/archive-attention rendering and safe text rendering.
- [ ] Add DOM tests for reports task history loading, empty state and navigation.
- [ ] Verify tests fail before implementation.
- [ ] Implement diagnostics panel and history list using `textContent`/DOM creation.
- [ ] Keep current selections in-page; label retry as a new submission, not resume.
- [ ] Run `node tests/task_failure_recovery_ui.cjs` and all relevant existing UI scripts.
- [ ] Commit `feat: add task failure recovery guidance to UI`.

### Task 5: Cross-flow regression, docs and candidate package

**Files:**
- Modify: `docs/UPGRADE_STATUS_AND_NEXT_STEPS_20261105.md`
- Modify: `docs/UPGRADE_DELIVERY_PACKAGE.md`
- Modify: `LOCAL_TEST/TEST_ITEMS.md`
- Test: all tests above and existing P3-B/C tests.

- [ ] Run the full relevant Python regression group and report pre-existing retired-endpoint failures separately.
- [ ] Run all relevant Node UI scripts, `node --check`, and `python -m compileall -q scripts`.
- [ ] Verify interrupted-task recovery, task-history API and archive-attention behavior.
- [ ] Build a source ZIP excluding databases, models, outputs, secrets, `.git`, temporary files and previous manifests.
- [ ] Verify ZIP CRC, path safety, file count and per-file SHA256 manifest.
- [ ] Record Windows/GUI/backup-restore items as pending until real-device execution.
- [ ] Commit `docs: record task diagnostics and recovery acceptance`.

## Deferred to P3-D

- Asynchronous evaluation/score-only task lifecycle and progress polling.
- User-confirmed cancel/retry for long-running operations.
- Persisted, privacy-reviewed request snapshots for optional draft restoration.
- Streaming large CSV import and paginated/frame-window previews.
