# P3-E History Browser Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the V2 historical telemetry browser scale through server-side catalog pagination, bounded full-range chart previews, server-side time-window zoom, and single-channel sample paging.

**Architecture:** Extend the existing read-only SQLite V2 access functions and HTTP routes; use indexed time keys and SQL window functions for bounded min/max overview points. Keep the browser UI on the existing page and add a single-channel detail table without cross-channel alignment.

**Tech Stack:** Python 3.8+ standard library sqlite3, existing vanilla JavaScript, pytest and Node contract tests.

**Spec:** docs/superpowers/specs/2026-11-07-p3e-history-browser-design.md

## Global Constraints

- No new runtime dependencies, services, Redis, Celery or database schema migration.
- Preserve V2 role/run-view and segment governance filters in every catalog/chart/page query.
- Preserve time-domain isolation and reject conflicting values/labels at the same logical time key.
- Keep SQLite reads read-only; do not modify source samples, labels or provenance.
- Full CSV export remains complete and is not capped by chart preview limits.
- Do not align different channels into one row-based table; detail paging is one channel at a time.
- frame_count counts distinct logical time keys; valid_sample_count counts non-null numeric logical samples; plot_point_count counts chart points returned; duplicate_count counts extra physical rows.

## Review Focus

- A database with many channels must not load all segment rows into Python to render one catalog page; SQL page and total count must agree.
- Identical duplicate samples must collapse consistently across overview and page modes; conflicting values or labels anywhere in the selected time window must still fail closed.
- Sparse or dense fault labels must not be silently hidden by overview decimation; at least one label=1 representative per bucket is retained within the point cap.
- Numeric, naive datetime and UTC time keys must preserve their original domains and precision when zooming; no timezone reinterpretation or rounding-induced sample loss.
- A stale or slower previous chart request must not overwrite a newer zoom/filter request.

## Tasks

1. SQL-backed catalog pagination and ancestor-filtered DISTINCT facets.
2. Bounded full-range chart overview and exact count metrics.
3. Single-channel sample detail cursor pagination with strict cursor validation.
4. Browser catalog pagination and server-backed zoom with latest-request-wins.
5. Single-channel detail UI with cursor stack and 50,000-frame stress verification.

## Verification summary

- P3-E Python regression: 186 passed, 1 deselected.
- Browser contract test: PASS.
- Other Node UI contracts: 15 PASS; two known base failures are recorded in P3E_PROGRESS_2026-11-07.md.
- Python compileall, JavaScript syntax and CRLF-aware diff check: PASS.
- Windows 10/11 acceptance remains in P3-H.
