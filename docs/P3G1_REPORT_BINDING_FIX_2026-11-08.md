# G1 report duplicate binding fix — actual source verification

Date: 2026-11-08

## Defect reproduced in full candidate
The complete G1 comparison-export candidate contained duplicate `exportComparisonG1Btn` IDs, duplicate DOM element bindings, duplicate `lastComparison` state keys, duplicate result-state assignment and duplicate export handler/event registration. The previous static patch had not been applied to that complete candidate.

## Actual source changes
Applied directly to the full extracted source tree:
- Removed one duplicate comparison export button from `reports.html`.
- Removed duplicate `exportComparisonBtn` binding.
- Removed duplicate `lastComparison` state key.
- Removed duplicate successful comparison assignment/enable operation.
- Removed duplicate `exportG1ComparisonJSON()` definition and duplicate click handler.
- Added `tests/report_g1_unique_binding.cjs` checking uniqueness of IDs, state, handler and event binding.

## Verification actually executed
- Before fix: reproduced counts of 2 for button ID, DOM binding, state key and handler; event binding also duplicated.
- After fix: `node tests/report_g1_unique_binding.cjs` — PASS.
- `node tests/report_comparison_export_contract.cjs` — PASS.
- `node tests/result_chart_evidence_semantics.cjs` — PASS.
- `node tests/result_chart_evidence_behavior.cjs` — PASS.
- `node tests/result_chart_g1_integration.cjs` — PASS.
- `node --check web/visualization/reports.js` — PASS.
- `python -m compileall -q scripts` — PASS.
- Official packer `python scripts/build_site_media_package.py pack` — PASS.
- ZIP: 676 entries; CRC PASS; SHA-256 `ff64787c52de3c9cbed70c2c937b110e30bfb3afbe71dd63225ac15b9d22b87c`.

## Candidate
`AAS_G1_REPORT_BINDING_FIX_20261108_candidate.zip`, 2,223,007 bytes.
Library path: `/AAS_G1_REPORT_BINDING_FIX_20261108_candidate.zip`.

## Remaining
This verifies the source and contract-level DOM binding fix. Live authenticated browser comparison and download still need real runtime acceptance. It does not complete simulated CSV export/import round-trip or Windows acceptance. No merge to main; PR remains Draft.