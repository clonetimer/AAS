# P3-G0-5 / G0-6 / G1 implementation checkpoint — 2026-11-08

## Source baseline
- Input: `AAS_V2_P3G0_R1-R4_G04_20261108_candidate.zip`
- New local working tree extracted from the verified candidate; source archive itself remains unchanged.
- Updated full source candidate: `AAS_V2_P3G0_R1-R4_G05_G06_G1_20261108_candidate.zip`
- Size: 3,484,867 bytes; 881 ZIP entries; 880 manifest entries.
- ZIP CRC: PASS; manifest SHA-256 mismatches: 0.
- ZIP SHA-256: `8c10ded89269ac390286bc2abe1d69004633c024a5b0eceb82170d91c00a9f27`.
- Library path: `/AAS_V2_P3G0_R1-R4_G05_G06_G1_20261108_candidate.zip`.

## G0-5 — safe runner draft persistence (implemented in full candidate)
- Added versioned `aas.runner.safe-draft.v1` allowlist, maximum 4 KiB.
- Stores only static choices: train/test source, selected user CSV dataset ID, start/end time, algorithm, evaluation model and split.
- Restore happens only after catalogs, algorithms and models have been loaded; then catalog is reloaded against the restored source and dataset.
- Select values not present in current options and malformed datetime values are rejected.
- CSV bytes, telemetry arrays, preview results, dynamic run/feature bindings, task IDs, execution receipts, credentials and preflight authorization are never persisted.
- A restored dataset choice is a preference only; existing preflight/preview state is not restored.
- New contract test: `tests/runner_safe_draft_contract.cjs` — PASS.

## G0-6 — canonical task status summary (implemented in full candidate)
- Added `build_task_history_summary(runtime, 50)` in `task_runtime.py`.
- `/api/task-history` returns one canonical `summary` projection for latest 50 tasks and a separate durable total.
- Home uses this summary instead of recounting 100 returned records.
- Task centre displays the same status counts and same 50-task scope.
- running+cancelling are grouped as running.
- Report-attention remains a completed-task subset and is not double-counted as failure.
- Query failure remains unavailable, not zero.
- New Python tests: `tests/test_task_history_summary_projection.py` — 2 passed.
- Existing task history UI and task-client behavior tests — PASS.

## G1 — result evidence interpretation (implemented in full candidate)
- Result chart now explicitly explains that anomaly scores are algorithm outputs, not probabilities.
- Decision threshold is only drawn when supplied by algorithm result evidence.
- Unknown ground-truth intervals remain unknown and are not treated as normal in supervised metrics.
- Existing event overlays and event metrics remain bound to actual result evidence.
- New contract test: `tests/result_chart_evidence_semantics.cjs` — PASS.
- This is a clarification/guardrail increment; it does not add new model post-processing or thresholding.

## Verification
- Focused R1-R3 and task summary Python suite: **35 passed**.
- Runner safe-draft contract: PASS.
- Task history UI behavior: PASS.
- Task client behavior: PASS.
- First-use CSV UI contract: PASS.
- G1 result evidence semantics: PASS.
- `node --check` on runner-v2.js, home.js, reports.js and result-chart.js: PASS.
- `python -m compileall -q scripts`: PASS.
- The source was extracted from a ZIP and has no Git metadata; a local `git diff --check` cannot be claimed for this working tree. Packaging checks above are actual.

## Remaining acceptance
- Browser live authenticated route interaction and screen-reader/keyboard acceptance are still not completed; environment policy has blocked direct localhost navigation in prior runs. Isolated DOM contract tests do not replace this.
- Windows 10/11 fresh-machine acceptance remains P3-H.
- Full historical project suite is not claimed green; known stale simulation UI assertion and missing targets fixture remain documented from R1-R3.
- PR remains Draft. The GitHub repository is Public and still does not contain the full cloneable source tree; the full source ZIP is in the Library.