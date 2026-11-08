# G1 JSON report round-trip — 2026-11-08

## Substantive source change
`web/visualization/result-chart.js` report snapshots now include the server-produced `event_metrics` alongside per-event evidence rows. Previously the snapshot included the event overlay but omitted the already archived aggregate event metrics, so the exported JSON was incomplete for independent review.

## Regression test
Extended `tests/result_chart_evidence_behavior.cjs` with a JSON stringify/parse round-trip that checks:
- supervised precision metric retained;
- event recall and evaluable/detected event counts retained;
- event ID and interval evidence retained;
- original result SHA-256 retained.

## Executed tests
- `node tests/result_chart_evidence_semantics.cjs` — PASS.
- `node tests/result_chart_evidence_behavior.cjs` — PASS, including round-trip assertions.
- `node tests/result_chart_g1_integration.cjs` — PASS.
- `node --check web/visualization/result-chart.js` — PASS.
- `node --check web/visualization/reports.js` — PASS.

This is an actual production JS source change and executable regression test. The JSON stringify/parse test is not a live authenticated browser download or installed-runtime archive round-trip. Those acceptance items remain open.

GitHub patch: `patches/P3G1_EVENT_METRICS_ROUNDTRIP.patch`.
