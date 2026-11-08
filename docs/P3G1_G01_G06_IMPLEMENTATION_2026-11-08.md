# G1-1 through G1-6 — implementation checkpoint

Date: 2026-11-08
Branch: feat/aas-persistent-user-csv-workspace

## G1-1 — experiment input evidence projection
Added reusable evidence-card projection for model, algorithm, source type/name/SHA-256, feature schema and label provenance. Missing evidence is rendered as unavailable; it is not inferred from UI defaults.

## G1-2 — shared event/time projection
Added event-row projection aligned to the result time vector. It calculates sample coverage, known-truth coverage, detections and first-detection delay only from supplied prediction/truth arrays. Unknown truth remains unknown.

## G1-3 — event-level interpretation
Event status is one of detected, not_detected, unknown_truth or no_samples. Detection delay is unavailable when no prediction occurs or event start is absent. It does not add alert smoothing, persistence rules or threshold post-processing.

## G1-4 — experiment comparability
Added a conservative evidence classifier. It refuses comparability when algorithm, source SHA-256, source type, label status or feature schema differ, and returns explicit mismatch keys. This helper is a presentation safeguard, not a replacement for the backend's existing stricter comparability decision.

## G1-5 — report snapshot
Added a versioned report snapshot projection with provenance, metrics only when truth is verified, algorithm-score semantics, supplied threshold only, event rows and result digest. No model output is fabricated.

## G1-6 — independent verification
Added `tests/result_chart_g1_evidence_behavior.cjs` to exercise unknown-label handling, event coverage/delay, source mismatch classification and report snapshot behavior. The test is published but must be run in the complete local source tree; no run result is claimed in this record yet.

## Published source
- Patch: `patches/P3G1_EVIDENCE_EVENT_REPORT_HELPERS.patch`
- Test: `AAS/tests/result_chart_g1_evidence_behavior.cjs`
- The patch is a source patch artifact. It has not yet been applied to the full candidate or wired into the production report export route. G1-1 through G1-6 are therefore implementation components, not accepted end-to-end features.

## Remaining integration and acceptance
1. Apply helpers at the actual result chart entry point and ensure input shapes match server response schema.
2. Integrate evidence and event rows into report details and export.
3. Use backend comparability verdict as authority; helper only explains mismatched fields.
4. Add report export/import round-trip and digest verification.
5. Run G1 test against real archived training/evaluation/score-only results.
6. Verify no score-only metrics and no unknown-truth samples counted as normal.
7. Live browser, keyboard/accessibility and Windows acceptance remain outstanding.
8. PR remains Draft.
