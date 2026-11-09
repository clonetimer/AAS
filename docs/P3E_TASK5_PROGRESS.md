# P3-E Task 5 — Detail UI and full 50,000-frame verification

Date: 2026-11-07

## Detail UI
- One detail channel is selected only from currently selected catalog channels.
- Previous/next cursor navigation is server-backed; page size is 100 logical frames.
- Table shows logical frame index, timestamp, numeric value, label, source table, segment ID and source-file row number.
- Changing channel, role, time range or selected-channel set invalidates pending detail requests and resets the cursor stack.
- No cross-channel row/time alignment is performed.

## Stress verification
- 50,000 logical frames; 50,001 physical rows; one identical duplicate; 49,000 valid numeric samples.
- 500 pages × 100 frames; logical indexes 1–50,000 contiguous.
- Full traversal 4.257 seconds; child peak RSS 160,436,224 bytes on Linux x86_64 / Python 3.13.5.
- A 30-second timeout protects against reintroduction of repeated full-table scans.

## Tests
- Python regression: 190 passed, 3 skipped, 1 known Phase 41 baseline test deselected.
- Browser P3-E contract: PASS.
- Other Node contracts: 15 PASS; two historical failures reproduced on P3-D base commit dc72030.
- compileall, JS syntax and CRLF-aware diff check: PASS.

Windows 10/11 acceptance remains part of P3-H.
