# AAS V2 P0 Auto-Archived Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each real V2 training/evaluation, reliably publish a verifiable, discoverable report and show it by default in the user-facing result history.

**Architecture:** Reuse `ExecutionRegistry.issue` and full-result evidence, but archive on the server before claiming the business operation fully complete. A small new writer publishes immutable report directories atomically; V2 routes invoke it and surface archive metadata, and report UI opens all types by default.

**Tech Stack:** Python 3.12 (project supports Windows Python 3.8+ where current code permits), stdlib gzip/json/pathlib/tempfile, pytest, existing server and vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-05-aas-v2-upgrade-design.md`

## Global Constraints

- Do not restore old `/api/save-experiment`, which was retired in Phase 53; no browser-authored results.
- Never overwrite an existing experiment path; reject unsafe IDs and use per-evaluation unique IDs.
- Only serialize server-frozen receipt data and `chart_trace`; no invented metrics or alarm decisions.
- Preserve `verify_experiment.verify()` digest contract, model registry, existing report listing/details contracts.
- Do not silently claim archive success on disk error; report the difference between compute and archive state.
- Use test-first RED→GREEN and targeted regression tests. Existing 410 legacy test failures and unavailable `torch` are pre-existing baseline conditions.

## Review Focus

1. Output directory already exists: archiver must not overwrite it; test in Task 1.
2. Input snapshot declared complete: writer must store snapshot matching verifier; test in Task 1.
3. Evaluation same model twice: unique report IDs and two entries; test in Task 2.
4. Archive write fails: route returns explicit `archive_status` and no false report success; test in Task 2.
5. Empty old report list: UI defaults to `all` and legacy `training` only reports remain discoverable; test in Task 3.

---

### Task 1: Server-side atomic archive writer

**Files:**
- Create: `scripts/server/v2_experiment_archive.py`
- Test: `tests/test_v2_auto_archive_upgrade.py`

**Interfaces:**
- Consumes `ExecutionRegistry.consume(token, kind)` and its record `{config,result,identity,provenance,input_snapshot}`.
- Produces `archive_receipt(root: Path, registry: ExecutionRegistry, *, kind: str, receipt: str) -> dict` containing `experiment_id`, `type`, `summary_path`, `archive_status='complete'`.

- [ ] **Step 1: Write failing tests**: a receipt from `registry.issue` writes all reports; `verify(summary)['status']=='verified_archive_digest'`; duplicate path and receipt reuse cannot overwrite; optional valid complete input snapshot persists and verifies.
- [ ] **Step 2: Run test**: `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py -k archive_writer` — Expected: failed import or missing archive writer.
- [ ] **Step 3: Implement** `archive_receipt` and private manifest builders. Use `tempfile.mkdtemp` under `outputs`, gzip the canonical frozen result, save config, save snapshot if declared complete, publish completed directory only after writing all files, ensure clean temporary-dir cleanup.
- [ ] **Step 4: Run test**: `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py -k archive_writer` — Expected: all new tests pass.
- [ ] **Step 5: Commit**: `git add scripts/server/v2_experiment_archive.py tests/test_v2_auto_archive_upgrade.py && git commit -m 'feat(aas): add immutable automatic V2 run archives'`.

### Task 2: V2 train/evaluate integration and report recovery

**Files:**
- Modify: `scripts/server/telemetry_v2_routes.py`
- Modify: `scripts/server/task_runtime.py` only if an archive-state projection needs extra fields.
- Test: `tests/test_v2_auto_archive_upgrade.py`

**Interfaces:**
- Calls Task 1 `archive_receipt` after validated execution, before `main.complete_task` / response success; metadata includes `archive_status`, `archive_id`, `summary_path`.

- [ ] **Step 1: Write failing route tests** using scoped fake V2 context and patched data selection; verify training async persists immediately, evaluation twice gets distinct reports, disk failure returns explicit archive error/state (not a made-up success).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py -k route` — Expected: FAIL for missing archive integration.
- [ ] **Step 3: Implement** V2 route calls to archive writer; training exposes report metadata even when model publication remains a separate action; archive failure is visible and does not destroy a successfully trained model; keep existing execution receipt for compatibility if needed, but it must not be used as a browser save permission.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py` — Expected: all new tests pass.
- [ ] **Step 5: Commit**: `git add scripts/server/telemetry_v2_routes.py scripts/server/task_runtime.py tests/test_v2_auto_archive_upgrade.py && git commit -m 'fix(aas): persist V2 training and evaluation history'` (skip unchanged files).

### Task 3: Default all reports and result navigation

**Files:**
- Modify: `web/visualization/reports.js`
- Modify: `web/visualization/reports.html` if active tab needs adjustment.
- Modify: `web/visualization/runner-v2.js`
- Test: `tests/test_v2_auto_archive_upgrade.py`

**Interfaces:**
- Consumes the `summary_path` and `archive_id` from Task 2; navigates to `reports.html#<urlencoded summary_path>`, used by existing `loadExperiments()`.

- [ ] **Step 1: Write failing UI-contract tests** that assert default `all` both in state and visible tab, and the run-result contains a report navigation link only when `archive_status=='complete'`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py -k ui` — Expected: FAIL before UI change.
- [ ] **Step 3: Implement** default all, report link after training/evaluation; avoid displaying one-time receipts as meaningful information to users.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q tests/test_v2_auto_archive_upgrade.py`, `node --check web/visualization/reports.js`, `node --check web/visualization/runner-v2.js` — Expected: PASS and clean JS syntax.
- [ ] **Step 5: Commit**: `git add web/visualization/reports.js web/visualization/reports.html web/visualization/runner-v2.js tests/test_v2_auto_archive_upgrade.py && git commit -m 'feat(aas): show all archived runs and navigate to new report'`.

### Task 4: P0 regression, evidence, and handoff

**Files:**
- Create: `docs/UPGRADE_P0_REPORT_CLOSURE.md`
- Test: `tests/test_v2_auto_archive_upgrade.py`, `tests/test_result_curve_archive.py`, `tests/test_experiment_comparison.py`

- [ ] **Step 1: Run** project test subsets and verify sample artifacts with `scripts/verify_experiment.py`. Expected: new tests and compatible report-read tests PASS; pre-existing retired API tests may still fail if included in the entire suite.
- [ ] **Step 2: Inspect** `git diff --check` and source changes, record actual tests and known environment failures in `docs/UPGRADE_P0_REPORT_CLOSURE.md`.
- [ ] **Step 3: Commit** `git add docs/UPGRADE_P0_REPORT_CLOSURE.md && git commit -m 'docs(aas): document P0 closure and test results'`.
