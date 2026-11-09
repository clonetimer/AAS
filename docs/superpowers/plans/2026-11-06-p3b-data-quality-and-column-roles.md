# P3-B Data Quality and Column Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Show actionable data-quality diagnostics before import, let users explicitly select time/label/measurement roles, and save reusable local mapping presets.

**Architecture:** Add one bounded quality analyzer over already parsed rows. Both simulation and history CSV inspection return the same summary shape. History import sends explicit `time_column`, `label_column`, and `selected_columns` through preview/commit into the wide importer. Browser mapping edits update a single state object; optional presets store only column-role/mapping metadata in localStorage.

**Tech Stack:** Python 3.11+, stdlib `csv`, `datetime`, `statistics`, existing SQLite import pipeline, browser JavaScript/localStorage, pytest, Node.js.

**Spec:** `docs/superpowers/specs/2026-11-06-p3b-data-quality-and-column-roles.md`

## Global Constraints

- Do not rewrite original CSV bytes or auto-clean missing/invalid values.
- Unknown labels remain unknown; never infer normal from missing labels.
- Time/label role selection does not bypass full preview/commit validation.
- The main telemetry DB remains unchanged until explicit commit; inspection is read-only.
- Simulation versions remain immutable; quality metadata is inherited across fault versions.
- Local presets store mapping metadata only, never CSV rows or raw data.

---

### Task 1: Shared bounded data-quality analyzer

**Files:**
- Create: `scripts/server/csv_quality.py`
- Test: `tests/test_csv_quality.py`

- [x] Add failing tests for missing/invalid/non-finite cells, label distribution, duplicate/descending timestamps, interval gaps, row numbers, and issue truncation.
- [x] Run the tests and verify expected failures.
- [x] Implement `analyze_csv_rows` with 50,000-row/66-column caller bounds and a maximum 20-item issue sample.
- [x] Run `pytest -q tests/test_csv_quality.py`.
- [x] Commit `feat: add bounded csv data quality analysis`.

### Task 2: History import quality and explicit time/label roles

**Files:**
- Modify: `scripts/server/telemetry_v2_source_import.py`
- Modify: `scripts/server/telemetry_v2_wide_import.py`
- Test: `tests/test_v2_source_file_import_phase33.py`
- Test: `tests/test_phase47_import_metadata_wizard.py`

- [x] Add failing tests for inspect quality summary, non-standard time/label names, explicit no-label mode, and selected-column projection.
- [x] Implement quality analysis in inspect and return role candidates/suggestions.
- [x] Extend `_selected_measurements` to preserve user-selected time/label columns and only selected measurement columns.
- [x] Extend `_prepare` to use explicit `time_column` and optional `label_column`, retaining defaults for older callers.
- [x] Run focused history import tests and ensure train/test/label gates remain intact.
- [x] Commit `feat: support explicit time label and measurement roles`.

### Task 3: Simulation quality summary and persistence

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Test: `tests/test_v2_csv_workspace_upgrade.py`

- [x] Add failing tests for quality counters, row issues, incomplete numeric columns, and quality metadata inherited by derived versions.
- [x] Add idempotent `quality_json` schema migration and return quality in inspect/import/list/detail.
- [x] Keep simulation numeric injection eligibility restricted to complete finite numeric columns.
- [x] Run simulation workspace tests, including pre-P3-B schema migration.
- [x] Commit `feat: show simulation csv data quality`.

### Task 4: History import role controls, quality panel and mapping-state correctness

**Files:**
- Modify: `web/visualization/import.html`
- Modify: `web/visualization/telemetry-v2-import.js`
- Create: `tests/telemetry_v2_import_quality_roles_ui.cjs`
- Test: `tests/test_v2_source_file_import_phase33.py`

- [x] Add failing browser tests for quality panel, time/label role selection, include/ignore toggles, and mapping edits reaching preview payload.
- [x] Add time/label selectors and per-column include checkboxes.
- [x] Fix mapping inputs so edited values update the state consumed by preview/commit (not merely the visible input).
- [x] Render quality counts and first 20 row/column issues safely via `textContent`.
- [x] Ensure selecting no label shows a clear warning and retains the existing no-label confirmation gate.
- [x] Run the browser test and history import regression tests.
- [x] Commit `feat: add quality and column role controls to history import`.

### Task 5: Local mapping presets

**Files:**
- Modify: `web/visualization/import.html`
- Modify: `web/visualization/telemetry-v2-import.js`
- Test: `tests/telemetry_v2_import_quality_roles_ui.cjs`

- [x] Add failing tests for save, reload, apply, mismatch rejection and delete.
- [x] Implement versioned localStorage presets containing header signature, time/label roles, included columns and metadata mappings only.
- [x] Apply presets by column name; reject non-matching column sets without silent partial mapping.
- [x] Add delete and clear status messages; preserve mapping CSV import/export for portability.
- [x] Run browser tests and verify private/disabled localStorage produces a useful message, not a crash.
- [x] Commit `feat: save local telemetry column mapping presets`.

### Task 6: Cross-flow regression and docs

**Files:**
- Modify: `docs/UPGRADE_STATUS_AND_NEXT_STEPS_20261105.md`
- Modify: `docs/UPGRADE_DELIVERY_PACKAGE.md`
- Test: `tests/test_csv_quality.py`, `tests/test_v2_source_file_import_phase33.py`, `tests/test_v2_csv_workspace_upgrade.py`, `tests/telemetry_v2_import_quality_roles_ui.cjs`, `tests/csv_workspace_ui.cjs`

- [x] Run all P3-B Python tests and browser scripts.
- [x] Run `python -m compileall -q scripts` and `node --check` on changed JS.
- [x] Verify old simulation DB schema migration and existing source DB read/write.
- [x] Record full-suite baseline failures separately from new regressions.
- [x] Update user-facing docs and package manifest after code review.
- [x] Commit `docs: record p3b data quality acceptance`.

## Next

- P3-C: experiment comparison UI and failure-stage explanations.
- P3-D: long-task progress/cancel/retry and large-data paginated preview.
