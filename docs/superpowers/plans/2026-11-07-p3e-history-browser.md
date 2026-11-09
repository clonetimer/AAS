# P3-E History Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the V2 historical telemetry browser scale through server-side catalog pagination, bounded full-range chart previews, server-side time-window zoom, and single-channel sample paging.

**Architecture:** Extend the existing read-only SQLite V2 access functions and HTTP routes; use indexed time keys and SQL window functions for bounded min/max overview points. Keep the browser UI on the existing page and add a single-channel detail table without cross-channel alignment.

**Tech Stack:** Python 3.8+ standard library `sqlite3`, existing vanilla JavaScript, pytest and Node contract tests.

**Spec:** `docs/superpowers/specs/2026-11-07-p3e-history-browser-design.md`

## Global Constraints

- No new runtime dependencies, services, Redis, Celery or database schema migration.
- Preserve V2 role/run-view and segment governance filters in every catalog/chart/page query.
- Preserve time-domain isolation and reject conflicting values/labels at the same logical time key.
- Keep SQLite reads read-only; do not modify source samples, labels or provenance.
- Full CSV export remains complete and is not capped by chart preview limits.
- Do not align different channels into one row-based table; detail paging is one channel at a time.
- `frame_count` counts distinct logical time keys; `valid_sample_count` counts non-null numeric logical samples; `plot_point_count` counts chart points returned; `duplicate_count` counts extra physical rows.

## Review Focus

- A database with many channels must not load all segment rows into Python to render one catalog page; SQL page and total count must agree.
- Identical duplicate samples must collapse consistently across overview and page modes; conflicting values or labels anywhere in the selected time window must still fail closed.
- Sparse or dense fault labels must not be silently hidden by overview decimation; at least one label=1 representative per bucket is retained within the point cap.
- Numeric, naive datetime and UTC time keys must preserve their original domains and precision when zooming; no timezone reinterpretation or rounding-induced sample loss.
- A stale or slower previous chart request must not overwrite a newer zoom/filter request.

---

### Task 1: SQL-backed catalog pagination and facet queries

**Files:**
- Modify: `scripts/server/telemetry_v2_access.py`
- Modify: `scripts/server/data_routes.py`
- Test: `tests/test_p3e_catalog_pagination.py`

**Interfaces:**
- `list_channels(db, *, role='unspecified', filters=None, start=None, end=None, limit=50, offset=0)` returns `{items, role, count, limit, offset, has_more, next_offset}`. `count` is total matching channels before pagination.
- `filter_options(db, *, role='unspecified')` returns the same facet keys as today, but uses DISTINCT queries rather than materializing all channel objects.

- [x] Write tests for stable ordering, first/middle/last pages, total count, filters, invalid limit/offset and role isolation.
- [x] Run tests and verify RED because pagination parameters and metadata are absent.
- [x] Implement SQL-level page selection and total count; load segment aggregates only for IDs in the current page.
- [x] Replace full catalog materialization in `filter_options` with DISTINCT facet queries.
- [x] Run `pytest tests/test_p3e_catalog_pagination.py tests/test_telemetry_v2_browser_phase4.py -q` and commit.

### Task 2: Bounded full-range chart overview and exact metrics

**Files:**
- Modify: `scripts/server/telemetry_v2_access.py`
- Modify: `scripts/server/data_routes.py`
- Test: `tests/test_p3e_chart_overview.py`

**Interfaces:**
- `query_charts(..., start=None, end=None, limit=3000, mode='overview', cursor=None)` supports overview and page modes.
- Overview mode returns at most `limit` valid chart points per channel, selected across the entire filtered time window using time buckets; it preserves bucket first/last, min/max and one fault-labelled representative.
- Each series returns `frame_count`, `valid_sample_count`, `plot_point_count`, `duplicate_count`, `truncated`, `coverage` and `window`.
- Conflicting values or labels at any timestamp in the requested window fail closed before decimation.

- [x] Write tests for full-range coverage, point cap, min/max and fault-label preservation, exact count metrics, invalid samples, duplicate collapse and conflict detection outside the first page.
- [x] Run tests and verify RED.
- [x] Implement a shared filtered-row CTE using the existing role, run-view, governance and time-domain predicates; use SQLite window functions for deterministic min/max envelope selection.
- [x] Run `pytest tests/test_p3e_chart_overview.py tests/test_telemetry_v2_browser_phase4.py tests/test_phase51_raw_row_storage.py -q` and commit.

### Task 3: Single-channel sample detail cursor pagination

**Files:**
- Modify: `scripts/server/telemetry_v2_access.py`
- Modify: `scripts/server/data_routes.py`
- Test: `tests/test_p3e_sample_pagination.py`

**Interfaces:**
- `query_charts(..., mode='page', channel_ids=[one_id], limit=100, cursor=None)` returns exact logical frames, including invalid numeric rows, in ascending time order.
- `next_cursor` is an opaque versioned cursor containing the last returned time key; `has_more` indicates whether another page exists.
- Page mode returns `page_row_count`, frame metrics and `frame_index` for each row; cursor input is strictly validated against the channel's time domain.

- [x] Write tests for 100-row pages, no duplicate/no missing rows, cursor validation, time-window interaction, invalid values, duplicate collapse and conflicting records.
- [x] Run tests and verify RED.
- [x] Implement keyset cursor encoding/decoding and indexed `time_key > cursor` paging.
- [x] Run `pytest tests/test_p3e_sample_pagination.py tests/test_p3e_chart_overview.py -q` and commit.

### Task 4: Browser catalog pagination and server-backed zoom

**Files:**
- Modify: `web/visualization/telemetry-v2-browser.js`
- Modify: `web/visualization/browser.html`
- Modify: `web/visualization/telemetry-v2-browser.css`
- Test: `tests/telemetry_v2_browser_p3e.cjs`

**Interfaces:**
- Catalog page size is 50; page controls display total and current page, preserve selected channel IDs across page changes, and reset to page 1 on filter changes.
- Initial plot uses `mode='overview'`; wheel zoom, zoom buttons and drag selection call the server with the new time window.
- Reset restores the initial selected time range; latest request wins when queries overlap.
- Plot metadata displays logical frame count, valid sample count, plotted point count and duplicate count separately.

- [x] Write Node contract tests for pagination, window requests, latest-request-wins, metrics labels and reset behavior; verify RED.
- [x] Implement page state, channel cache, server window reload and explicit metrics display.
- [x] Run `node tests/telemetry_v2_browser_p3e.cjs` plus existing browser UI contracts and `node --check`; commit.

### Task 5: Paginated single-channel sample detail UI and stress verification

**Files:**
- Modify: `web/visualization/browser.html`
- Modify: `web/visualization/telemetry-v2-browser.js`
- Modify: `web/visualization/telemetry-v2-browser.css`
- Test: `tests/telemetry_v2_browser_p3e.cjs`
- Test: `tests/test_p3e_browser_stress.py`
- Create: `docs/P3E_PROGRESS_2026-11-07.md`

**Interfaces:**
- The detail panel selects one currently selected channel; it never joins multiple channels by row/time.
- Previous/next controls use a cursor stack; each page displays up to 100 logical frames and source provenance.
- Stress fixture contains at least 50,000 logical frames and validates bounded overview size, page continuity and count consistency.

- [x] Add tests for detail table selection, next/previous cursor navigation, page row count and provenance display; verify RED.
- [x] Implement detail table and cursor navigation.
- [x] Run the 50,000-frame stress test and record runtime/peak RSS as environment-specific measurements, including a full 500-page traversal.
- [x] Run the full relevant Python/Node regression set, `compileall`, `node --check` and `git diff --check`; commit the progress report.
