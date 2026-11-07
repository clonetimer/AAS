# AAS V2 P3-E Progress — 2026-11-07

## Conclusion

P3-E (historical database large-data browsing) is complete locally. The branch implements server-side catalog pagination, bounded full-range chart overview, server-backed zoom/selection, and single-channel cursor detail pages.

## Delivered

1. **Catalog pagination** — SQLite count + deterministic 50-item pages; hierarchy facets use DISTINCT queries with ancestor filters; selected channels remain selected across pages.
2. **Chart overview** — bounded full-range envelope; logical frame count, valid sample count, plotted point count and duplicate physical row count remain distinct; CSV export remains complete.
3. **Server-backed zoom** — wheel, zoom buttons and drag selection query a new server time window; reset restores the original range; latest-request-wins prevents stale responses from replacing current state.
4. **Time precision** — numeric, naive datetime and UTC datetime domains retain their semantics and six-digit microsecond precision.
5. **Single-channel detail** — only one selected channel is queried at a time; cursor previous/next pages show logical frame, timestamp, value, label, source table, segment ID and source row; no cross-channel alignment is introduced.

## 50,000-frame full traversal

- Logical frames: 50,000
- Physical rows: 50,001 (one identical duplicate)
- Valid numeric samples: 49,000
- Overview points: 2,995 (cap 3,000)
- Detail traversal: 500 pages × 100 logical frames; indexes 1–50,000 contiguous, no gaps or repeats
- Full traversal time: 4.257 seconds on Linux x86_64 / Python 3.13.5
- Child-process peak RSS: 160,436,224 bytes (~153 MiB)

These are Linux test-host measurements, not Windows acceptance results. The stress test has a 30-second timeout to catch repeated full-table scans.

## Cursor performance correction

The first complete 500-page traversal exceeded the 20-second guard. The cause was repeated full-window statistics/conflict scans, prefix frame count and window sorting on every page.

The correction:
- upgrades the opaque cursor to v2 with a SHA-256 fingerprint over the SQLite main database, WAL and journal file metadata (including canonical database path);
- caches validated full-window metrics in a bounded process-local LRU (4,096 entries);
- invalidates cursors on database-file changes, including value-only changes that preserve row counts/time keys;
- falls back to full validation after service restart or cache eviction;
- uses indexed time-key pagination and computes row numbers only for the current page candidates;
- rejects sample/source-segment time-domain mismatches rather than silently filtering them.

## Verification

- P3-E + related V2 Python regression: **190 passed, 3 skipped, 1 known Phase 41 baseline test deselected**.
- P3-E browser contract: PASS.
- Node UI contracts: 15 PASS; two historical tests (runner_selection_behavior.cjs, task_failure_recovery_ui.cjs) still fail on the P3-D base commit dc72030, so they are not P3-E regressions.
- Python compileall, browser JS syntax and CRLF-aware diff check: PASS.
- Windows 10/11 fresh-install acceptance remains in P3-H.

## Source archive

- Full source candidate: AAS_V2_P3E_20261107_candidate.zip
- Size: 2,105,932 bytes
- ZIP entries: 686; CRC: PASS
- SHA-256: a42d59a55a6e4d0a25c4a5c9a68d95a6b14b4d5e4db0f8dbcbab8b95a1d11c9b

The candidate ZIP is delivered separately in the ChatGPT conversation and is not stored as a binary asset in this repository. This remote repository still does not contain the complete application source tree; do not treat it as a complete cloneable application.
