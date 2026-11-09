# P3-G0 R4 — first-use CSV workspace guidance and responsive UI review

Date: 2026-11-08
Branch: feat/aas-r1r3-complete
Local commit: e0cf0ad

## User-facing changes

The training and evaluation source pickers now each include an expandable explanation that answers why their CSV is not imported into the historical telemetry database.

Training guidance explains:
- The file is saved only in the persistent “My Training CSV” workspace.
- It is not listed by the historical database browser and does not modify historical telemetry.
- It remains available across application restarts until explicit user deletion.
- A dataset referenced by a running or unresolved task cannot be deleted.

Evaluation guidance explains:
- The file is saved only in the persistent “My Test CSV” workspace.
- Existing 0/1 labels are preserved.
- Missing labels remain unknown; they are not converted to normal and do not generate supervised metrics.
- The file remains available until explicit user deletion.

Long metadata text now wraps inside the picker instead of widening the page. Existing confirmation dialogs continue to state that deleting a CSV does not delete published model artifacts or experiment reports.

## Verification

- New static UI contract test: tests/runner_csv_first_use_help.cjs — PASS.
- Existing CSV UI contracts: csv_workspace_ui.cjs, runner_csv_async_task_ui.cjs, runner_csv_format_behavior.cjs, runner_csv_import_ui.cjs, runner_score_only_ui.cjs — all PASS.
- R1–R3 focused Python tests: 22 passed.
- JavaScript syntax checks, Python compileall, git diff --check — PASS.
- Chromium isolated rendering of the actual runner HTML and CSS at 1440, 1024, 768, 390 and 320 px: no horizontal overflow; both train/test CSV import sections present.
- Direct browser navigation to the local application remains blocked by environment policy (ERR_BLOCKED_BY_ADMINISTRATOR). The isolated render is component/page markup evidence only, not a live API/browser E2E or Windows acceptance.

## Candidate package

- AAS_V2_P3G0_R1-R4_20261108_candidate.zip
- 2,209,989 bytes; 673 ZIP entries; 672 manifest files.
- ZIP CRC PASS; all per-file SHA-256 hashes PASS.
- SHA-256: 4d28a5322efa7230f92a1f3de99117adcc49bac6d4751ccd4761dbc97b11803b.
- The full source package is saved in the ChatGPT Library at /AAS_V2_P3G0_R1-R4_20261108_candidate.zip.

## Remaining

R4 is not fully closed: live browser route interaction, keyboard/assistive technology acceptance, deletion confirmation in a real authenticated session and Windows 10/11 fresh-machine acceptance remain outstanding. PR stays Draft; no merge to main.
