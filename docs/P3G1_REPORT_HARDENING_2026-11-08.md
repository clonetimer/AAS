# G1 report production hardening — 2026-11-08

## Substantive source changes
The full candidate source was modified and the actual production JavaScript re-tested; this was not a documentation-only increment.

1. `web/visualization/result-chart.js`: report snapshot now preserves a null/absent decision threshold as unavailable. Previously `Number(null)` became `0`, which could falsely report a zero threshold when the algorithm supplied none.
2. `web/visualization/reports.js`: archived event ID, type, start and end values are HTML-escaped before insertion into the report event table. Event records are data and must not be interpreted as markup.
3. `tests/result_chart_evidence_behavior.cjs`: added regression assertions for null threshold semantics and event-field escaping.

## Verification actually run
- `node tests/result_chart_g1_integration.cjs` — PASS.
- `node tests/result_chart_evidence_semantics.cjs` — PASS.
- `node tests/result_chart_evidence_behavior.cjs` — PASS, including new null-threshold and escaping assertions.
- `node --check web/visualization/result-chart.js` — PASS.
- `node --check web/visualization/reports.js` — PASS.
- `python -m compileall -q scripts` — PASS.
- ZIP integrity: 884 entries, CRC PASS.
- Delivery manifest: 883 file entries, zero SHA-256 mismatches.
- ZIP SHA-256: `685466939b07357eafab853b1f5f927d649eff901234e1a3bdfb6fb1adf72c3b`.
- ZIP size: 3,497,426 bytes.

## Candidate
`AAS_V2_P3G1_REPORT_INTEGRATION_20261108_candidate_v2.zip`
Library path: `/AAS_V2_P3G1_REPORT_INTEGRATION_20261108_candidate_v2.zip`.

## Acceptance still outstanding
- Live authenticated browser export/download and JSON readback round-trip.
- Real archived examples covering training, supervised evaluation and score-only inference in an installed AAS runtime.
- Keyboard/screen-reader acceptance and Windows 10/11 fresh-machine acceptance.
- No claim of full project suite green or Stable.

The extracted candidate has no Git metadata; this package is a verified full-source snapshot. GitHub PR remains Draft and the Public repository still contains sanitized patch/progress records rather than the full source tree.