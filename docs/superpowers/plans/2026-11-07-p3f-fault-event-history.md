# P3-F Fault Event History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable fault-event timeline, single-event retraction, strict ancestor rollback and bounded version comparison to the immutable CSV simulation workspace.

**Architecture:** Extend the existing SQLite V2 snapshot header with an additive `operations_json` field. Preserve immutable frame snapshots; replay active events from the original CSV when retracting one event; rollback by creating a new child snapshot from an ancestor. Add authenticated HTTP routes and UI confirmation flows.

**Tech Stack:** Python standard library (`sqlite3`, `json`, `hashlib`), existing HTTP route mixins, vanilla JavaScript, pytest and Node UI contracts.

**Spec:** `docs/superpowers/specs/2026-11-07-p3f-fault-event-history-design.md`

## Global Constraints

- Preserve immutable snapshots; never overwrite or delete a parent version.
- Event ranges are 1-based inclusive frame intervals.
- Only events on the same channel are overlap candidates.
- Old event records without status remain active.
- Retraction and rollback require a matching preview digest.
- Rollback target must be a strict ancestor of the current version.
- Compare versions only within one original CSV import chain.
- Compare statistics scan all frames; chart points <= 2,000 and ranges <= 1,000.
- CSV export remains complete; only active events mark injected rows.
- No new runtime dependencies or background services.

## Review Focus

1. Old SQLite databases without `operations_json` still read and migrate additively — test legacy event migration.
2. Event order changes the result for overlapping bias/stuck events — test replay from original source.
3. Preview/commit races or stale preview hashes must not create a snapshot — test mismatched digest.
4. A sibling version must never be accepted as rollback target — test branch isolation.
5. Missing frame rows must not produce a partial comparison — test fail-closed sequence/count validation.

## Tasks

### Task 1: Event ledger normalization and summary-only version list

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Test: `tests/test_p3f_fault_event_history.py`

- [x] Write failing tests for legacy defaults, status validation, event sequence and summary-only list results.
- [x] Verify RED against P3-E baseline.
- [x] Add event normalization and additive `operations_json` migration.
- [x] Keep full event ledger out of `list_datasets`; return counts only.
- [x] Verify targeted workspace regression.

### Task 2: Event timeline and overlap explanation

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Modify: `scripts/server/handler_get.py`
- Test: `tests/test_p3f_fault_event_history.py`

- [x] Test same-channel closed-interval overlap and cross-channel non-overlap.
- [x] Implement ordered event timeline and operation history.
- [x] Add authenticated `GET /events` and `GET /ancestors` routes.
- [x] Verify route and workspace regression.

### Task 3: Single-event retraction with deterministic replay

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Modify: `scripts/server/handler_post.py`
- Test: `tests/test_p3f_fault_event_history.py`

- [x] Test that retraction replays remaining active events from the original CSV.
- [x] Test stale preview digest rejection and parent immutability.
- [x] Implement preview, digest validation, replay and immutable child snapshot.
- [x] Add preview/commit HTTP routes.
- [x] Verify old snapshots and export/chart active-event semantics.

### Task 4: Ancestor rollback and version comparison

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Modify: `scripts/server/handler_get.py`
- Modify: `scripts/server/handler_post.py`
- Test: `tests/test_p3f_fault_event_history.py`

- [x] Test strict ancestor-only rollback and event status reconciliation.
- [x] Test full changed-frame/cell counts, changed ranges and event deltas.
- [x] Implement read-only streaming comparison with bounded chart points/ranges.
- [x] Add rollback and comparison HTTP routes.
- [x] Verify 50,000-frame compare/retract/rollback and missing-row fail-closed behavior.

### Task 5: Timeline, comparison and rollback UI

**Files:**
- Modify: `web/visualization/simulation.html`
- Modify: `web/visualization/simulation-csv-workspace.js`
- Modify: `tests/csv_workspace_ui.cjs`
- Create: `tests/p3f_fault_history_ui.cjs`
- Test: `tests/test_p3f_fault_event_history.py`

- [x] Add browser contract test for timeline, event retract, comparison and rollback confirmation flows.
- [x] Implement event status/overlap rendering, event retract confirmation, ancestor selection, comparison overlay and rollback confirmation.
- [x] Verify Node syntax and browser contract.

### Task 6: Regression, review and delivery

**Files:**
- Create: `docs/P3F_PROGRESS_2026-11-07.md`
- Create: `docs/P3F_REGRESSION_2026-11-07.md`
- Create: `docs/P3F_PATCH_BASELINE.txt`

- [x] Run complete relevant regression set and compare failures with the P3-E baseline (98 passed, 3 skipped; eight baseline failures reproduced).
- [x] Review schema migration, event replay semantics, API authorization and UI race handling.
- [ ] Build candidate ZIP and verify ZIP CRC and SHA-256.
- [ ] Commit feature branch and prepare remote patch archive.
