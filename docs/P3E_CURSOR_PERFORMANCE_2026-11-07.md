# P3-E Cursor Pagination Performance Fix — 2026-11-07

## Root cause

A full 50,000-frame / 500-page traversal exceeded the 20-second guard. Each cursor page repeated full-window physical counts, logical-frame and valid-sample metrics, conflict audit, prefix frame count and a window-function sort over the remaining samples.

## Fix

- Cursor v2 includes a SHA-256 fingerprint derived from canonical DB path plus main DB/WAL/journal file metadata.
- A bounded process-local LRU (4,096 cursor snapshots) reuses the first page's validated metrics and full-window conflict audit while the fingerprint and query identity remain unchanged.
- Service restart/cache eviction falls back to full validation and prefix-frame-index verification; cursors are not persisted.
- A changed database fingerprint invalidates the cursor even if counts and time keys remain unchanged.
- Page SQL selects at most 101 logical time keys in indexed channel/time-domain/time-key order, then de-duplicates and numbers only those page candidates.
- Page mode rejects sample/source-segment time-domain mismatches instead of silently hiding malformed rows.

## RED → GREEN evidence

1. Added a full 50,000-frame traversal test with a 20-second guard. It failed before the fix with a subprocess timeout.
2. Added a value-only mutation test where time keys and row counts remain unchanged. The old cursor was accepted before the fix.
3. Added a time-domain mismatch test. The old page path silently omitted the malformed row before the fix.
4. All three tests pass after the fix.

## Verification

- 500 pages / 50,000 frames, contiguous frame indexes, no missing or repeated frames.
- Full walk: 4.257 seconds; peak RSS: 160,436,224 bytes on this Linux test host.
- Related Python regression: 190 passed, 3 skipped, 1 known baseline test deselected.
- Browser contract: PASS; 15 other Node contracts PASS, two pre-existing Node failures reproduced on base commit dc72030.
- compileall, JS syntax and CRLF-aware diff check: PASS.

The filesystem fingerprint is a conservative change token, not a full content hash. Cache is process-local and bounded; a cursor after restart remains correct but its first continuation performs full revalidation.
