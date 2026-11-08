# G0-4 CSV fault parameter guidance — 2026-11-08

## Implementation
- Local source commit: 0d63ca2b0b9d48d44033d47895ea948ff9a5ae74.
- Added dynamic explanation and label for the CSV simulation workspace's four built-in fault types.
- Bias: constant additive delta in selected inclusive frame range; sign determines direction.
- Drift: linear ramp ending at the configured cumulative delta.
- Stuck: replace each selected frame's value with a fixed target; zero is valid.
- Spike: periodic additive spike every 8 frames; magnitude is the additive delta.
- Explanations refresh immediately when fault type changes.
- Added test that checks the UI explanations against the backend spike implementation so the text cannot silently drift from code.
- Patch published at patches/P3G0_G04_FAULT_GUIDANCE.patch.

## Verification
- node --check web/visualization/simulation-csv-workspace.js — PASS.
- node tests/simulation_csv_fault_guidance.cjs — PASS.
- git diff --check — PASS.
- This is a focused source/contract test, not a live authenticated browser/API test.

## Remaining
- Extend the same explanatory treatment to plugin fault schemas in the historical simulator, with units sourced from plugin metadata rather than invented defaults.
- Live browser interaction and Windows acceptance remain outstanding.
- R4 remains open until live route interaction, keyboard/assistive technology, deletion guard and Windows checks are completed.
- PR remains Draft.
