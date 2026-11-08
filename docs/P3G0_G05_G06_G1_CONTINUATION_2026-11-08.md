# P3-G0-5/6 and G1 continuation plan — 2026-11-08

## G0-5 safe draft persistence — source patch published
- Added bounded 4 KiB, versioned allowlist for static train/test source preferences, dataset selectors, time bounds, algorithm and model/split selections.
- Restores only values still present in current select options; invalid/stale values are ignored.
- Does not persist CSV bytes, telemetry arrays, previews, dynamic feature/run bindings, task IDs, execution receipts, credentials or preflight authorization.
- Restored selections remain preferences and require fresh catalog reload and preflight before execution.
- Patch: `patches/P3G0_G05_SAFE_RUNNER_DRAFTS.patch`.
- Remote patch commit: `45bdf65c10e79901d5a962fed372fae49967c571`.
- Important: this patch has been published as a patch artifact; it has not yet been applied to and tested in the full source worktree. Treat G0-5 as implementation proposed/published, not accepted.

## G0-6 task-center convergence — review findings
- Home currently counts statuses over `/api/task-history?limit=100&include_all=1`.
- Task center currently loads `/api/task-history?limit=50&include_all=1` and displays the durable total separately.
- Both map running/cancelling, completed, failed, cancelled, interrupted and attention, but their sample windows differ. Therefore percentages/counts are not directly comparable.
- Required change: define one shared summary projection and explicit scope (latest N plus durable total), ensure report-attention is a completed subset, and represent unavailable as unavailable rather than zero. Do not add a second independent status mapper.
- No G0-6 production code change claimed in this checkpoint.

## G1 result interpretation — existing baseline capabilities
- `result-chart.js` already renders score and decision-threshold traces, predicted labels, true labels, event bands and event-level metrics; it treats unlabelled periods as unknown.
- `reports.js` already calls `/api/compare-experiments`, renders comparability classification/reason and metric deltas only when backend returns comparable evidence.
- G1 next implementation should consolidate provenance and comparability explanation, distinguish unavailable score/threshold/ground truth, and expose same-time event/score interpretation without inventing thresholding or post-processing rules.
- No new G1 production implementation claimed in this checkpoint.

## Verification boundary
- The G0-5 patch was pushed as a patch artifact only. It still requires application to the full source tree, node contract test, runtime check of all selector IDs, and tests proving that stale values do not bypass catalog/preview/preflight.
- G0-6 and G1 remain review/design work, not completed.
- PR remains Draft; do not merge until patches are applied to the full source candidate and regression tests pass.
