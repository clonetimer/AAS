# G1 production report integration — 2026-11-08

## Source baseline
- Base: AAS_V2_P3G0_R1-R4_G05_G06_G1_20261108_candidate_v2.zip.
- Updated candidate: AAS_V2_P3G1_REPORT_INTEGRATION_20261108_candidate.zip.
- Size: 3,570,489 bytes.
- ZIP CRC: PASS.
- ZIP SHA-256: `732f7312e9c1aa527343b85960998822275860e3931eed34357c3a718e9b08c6`.
- Full source saved in Library: `/AAS_V2_P3G1_REPORT_INTEGRATION_20261108_candidate.zip`.

## Production integration
- `result-chart.js` now exports `evidenceSummary`, `eventEvidence` and `buildReportSnapshot` through the existing `AASResultChart` namespace.
- Evidence projection reads the actual archived `chart_trace` and server provenance. It exposes algorithm, source name/type/hash, feature schema and label provenance.
- Event evidence uses the archived event overlay and preserves half-open `[start,end)` interval semantics.
- `reports.html` adds evidence and event sections plus a JSON evidence export action.
- `reports.js` uses the real `rows[].chart_trace` returned by `/api/experiment-details`, not an invented top-level result field.
- JSON report export fetches the selected archived experiment detail and creates a versioned evidence snapshot.
- Supervised metrics are included only when label provenance is explicitly `verified`; score-only or unknown truth never becomes normal.
- Algorithm score is explicitly not a probability; threshold is included only if supplied by the algorithm result.

## Verification
- `node tests/result_chart_evidence_semantics.cjs` — PASS.
- `node tests/result_chart_evidence_behavior.cjs` — PASS.
- `node tests/result_chart_g1_integration.cjs` — PASS.
- `node --check web/visualization/result-chart.js` — PASS.
- `node --check web/visualization/reports.js` — PASS.
- `python -m compileall -q scripts` — PASS.
- ZIP integrity — PASS.

## Limitations
- The new JSON export has a static/integration contract test but has not yet been exercised through a live authenticated browser session.
- Event evidence in the report reflects the archived event overlay; event-level metric calculation remains server-owned and is not recomputed in the browser.
- Report snapshot currently uses existing archived chart trace and provenance. If an older archive lacks source hash or label provenance, it displays unavailable/unverified rather than inferring values.
- Browser route interaction, keyboard/screen-reader acceptance and Windows 10/11 fresh-machine acceptance remain outstanding.
- GitHub repository is Public; this is a sanitized progress record. Complete candidate ZIP is saved in Library, not pushed as a complete source tree. PR remains Draft.
