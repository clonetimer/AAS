# G1 comparison report export — 2026-11-08

## Substantive implementation
Modified production files in the complete local source tree:
- `web/visualization/reports.html`: adds an initially disabled “导出比较报告 JSON” action next to the experiment comparison control.
- `web/visualization/reports.js`: retains the last successful server comparison response with the two experiment IDs; enables export only after successful comparison; invalidates the prior export if a new comparison request fails; exports a versioned JSON report with the original server response and explicit `server_compare_experiments` authority marker.

The browser does not recalculate comparability or metrics. The backend's classification, reason, repeatability controls and metric rows are exported unchanged. An unsuccessful or absent comparison cannot be exported.

## Verification
- `node tests/report_comparison_export_contract.cjs` — PASS.
- `node tests/result_chart_evidence_semantics.cjs` — PASS.
- `node tests/result_chart_evidence_behavior.cjs` — PASS.
- `node tests/result_chart_g1_integration.cjs` — PASS.
- `node --check web/visualization/reports.js` — PASS.
- `python -m compileall -q scripts` — PASS.
- Official project packer: 674 manifest entries, 675 ZIP entries.
- ZIP CRC: PASS; manifest SHA-256 mismatches: 0.
- ZIP SHA-256: `1aaa7e9ffaf7fcf4ef1406765a55de4b5a7bd502340bc2a9ba8d13fb5ab6931a`.
- ZIP size: 2,223,007 bytes.

## Candidate
`AAS_V2_P3G1_COMPARISON_EXPORT_20261108_candidate.zip`
Library path: `/AAS_V2_P3G1_COMPARISON_EXPORT_20261108_candidate.zip`.

## Boundaries
The comparison JSON action has a source contract test; live browser download and subsequent JSON file readback remain to be performed in an allowed authenticated runtime. No real production archives were available in this isolated build context, so this round does not claim training/evaluation/score-only production archive replay. PR remains Draft; no main merge.