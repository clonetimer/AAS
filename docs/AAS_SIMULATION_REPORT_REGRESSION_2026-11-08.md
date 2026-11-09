# AAS simulation and G1 report regression — 2026-11-08

## Substantive verification
The full candidate ZIP `AAS_G1_REPORT_BINDING_FIX_20261108_candidate.zip` was extracted and tested, not merely reviewed through docs.

### Simulation CSV end-to-end round-trip
Executed against the real `scripts/server/v2_csv_workspace.py` implementation in a temporary SQLite workspace:
1. Imported a four-frame CSV.
2. Injected bias into `gyro_x` (frames 2–3), then drift into the same `gyro_x` (frames 1–4), then stuck value into the same `gyro_x` (frames 3–4).
3. Verified immutable version v3 and three ordered fault events on the same parameter.
4. Exported both spreadsheet-safe and raw CSV variants.
5. Parsed each export and verified four frame rows, 1-based frame sequence and event markers.
6. Re-imported the original logical data columns from each export and verified four frames.

Added permanent test: `tests/test_simulation_csv_export_roundtrip.py`.

### Regression suites executed
- `test_v2_csv_workspace_upgrade.py`
- `test_v2_simulation_injection_phase13.py`
- `test_simulation_fault_param_ui_contract.py`
- `test_simulation_fault_table_preview_ui_contract.py`
- `test_simulation_record_filters.py`
- `test_v2_historical_export_upgrade.py`

Result: **50 passed, 3 skipped**.

### G1 report binding fix
- `report_g1_unique_binding.cjs` — PASS.
- `report_comparison_export_contract.cjs` — PASS.
- `result_chart_evidence_semantics.cjs` — PASS.
- `result_chart_evidence_behavior.cjs` — PASS.
- `result_chart_g1_integration.cjs` — PASS.

The report fix is present in the complete candidate and the DOM contract test passed. No live authenticated browser test is claimed.

## Feature conclusions from source and runtime evidence
- Simulation CSV export: implemented; both spreadsheet-safe and raw variants tested.
- X-axis frame number: implemented; 1-based frame display and inclusive injection ranges present in source and regression coverage.
- Multiple faults on the same parameter: implemented; actual three-step same-channel injection verified and permanent round-trip test added.
- Separate CSV destinations: source and previous R1–R3 end-to-end suite support separate simulation, train and test workspaces; this current run focused on simulation. Full all-three-entrance HTTP acceptance still needs a consolidated installed-runtime run.
- Reports UI: report evidence/event and comparison JSON export exist; duplicate DOM IDs/handlers have been fixed and static/behavior contracts pass. Live browser download/readback and visual/accessibility acceptance remain outstanding.

## Remaining acceptance
- Run the new permanent Python test in CI/local target environment.
- Verify three CSV entry points in one authenticated HTTP session, including history DB hash before/after.
- Browser download/readback of both report JSON and simulation CSV.
- Windows 10/11 fresh-install and GUI acceptance.
- PR remains Draft; no main merge.
