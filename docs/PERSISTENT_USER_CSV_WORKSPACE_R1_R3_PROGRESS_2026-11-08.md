# G0-3 R1–R3 continuation checkpoint

Date: 2026-11-08
Branch: `feat/aas-persistent-user-csv-workspace`
PR: #1 (Draft)

## R1 — persistent train/test CSV workspace

### Implemented source
- `v2_user_csv_workspace.py`: persistent independent SQLite file at `datasets/user_csv_workspaces/user_csv_workspace.sqlite`; stores raw bytes, SHA-256, role, metadata and creation time.
- Task reference table prevents deletion while a dataset is referenced.
- `v2_user_csv_adapter.py`: commits a confirmed upload using the existing V2 source importer with the workspace SQLite path as target; records resulting logical source ID while preserving the raw upload.
- Adapter rejects role mismatch and missing explicit format confirmation.

### Important limitation
R1 is **not end-to-end complete**. The actual `telemetry_v2_routes.py` currently resolves `_db(main)` to the active historical database. The adapter is not yet wired into authenticated HTTP routes, runner source selectors, task snapshots, the algorithm subprocess database argument, model provenance or report archival. Existing train/evaluate/score-only calls therefore still use the active V2 DB. Do not merge or claim user CSV training/evaluation works end-to-end yet.

### Verification
- Foundation lifecycle + adapter isolated tests previously recorded: 7 passed.
- Added restart persistence test: task references remain after a fresh DB open, prevent deletion, and allow explicit deletion after release.
- Adapter's prior test uses a controlled importer stub; a real `source_operation_file` commit against a workspace DB and actual algorithm worker are still required.

## R2 — simulation workspace

Existing `v2_csv_workspace.py` is already a separate simulation DB implementation:
- DB: `datasets/aas_simulation_v2.sqlite`, not the historical telemetry DB.
- CSV import creates immutable version 0.
- Each fault save creates a child snapshot with parent ID, version, content digest and event ledger.
- Multi-step injection, preview digest, version comparison, rollback-as-new-child and full CSV export are implemented.
- Original import and prior snapshots are not overwritten.

No duplicate simulation storage implementation was added.

## R3 — isolation and restart acceptance

Added `tests/test_v2_simulation_workspace_r1r3.py`:
- Import original CSV, inject bias into frames 2–3, then stuck fault into frames 3–4.
- Reopen/query the persisted SQLite database and verify version 2, parent chain, event history and all frames.
- Roll back to original ancestor as a new child version (version 3); original version 0 and version 1 remain unchanged.
- Compare historical DB bytes and SHA-256 before/after; unchanged.
- Verify simulation DB path differs from historical DB path.

Local isolated run: **2 passed**.

Added `tests/test_v2_user_csv_workspace_restart.py`:
- Persist a task reference, reopen through a fresh workspace DB operation, verify active reference count.
- Explicit delete is rejected while referenced.
- After explicit task reference release, user-confirmed deletion succeeds.

The simulation regression group also ran against the packaged source:
- `test_v2_csv_workspace_upgrade.py` + `test_simulation_database_lifecycle.py` + `test_v2_simulation_injection_phase13.py`: **41 passed, 2 failed, 3 skipped**.
- Both failures are environment/baseline fixture mismatches: an obsolete UI assertion expects `deleteSimBtn` absent from the current simulation UI, and a lifecycle test requires an unbundled `datasets/source_labels/targets.zip`. The second simulation hierarchical group also has a missing preinstalled demo DB fixture. These are not reported as green; target fixture/DOM compatibility needs separate reconciliation.

## R4 / next gate
- Finish authenticated HTTP endpoints for user CSV inspect/import/list/get/delete.
- Route training/evaluation/score-only to a validated workspace DB snapshot; keep active history as an explicit separate source option.
- Bind/release task references in the durable task lifecycle, including restart recovery and terminal task handling.
- Verify real importer commit, real algorithm subprocess, train→publish→CSV evaluate, score-only with NULL labels and report provenance.
- Add task-aware deletion guard to simulation datasets if tasks consume them; simulation workspace currently has immutable versions but is not yet wired to task retention.
- Then perform browser UX, first-use guide, R4 error recovery, and Windows acceptance.

## Source and publication boundary
This PR is a patch-oriented feature branch on a Public repository. It currently includes the workspace module, adapter, tests and progress docs, not a complete cloneable AAS source tree. No user telemetry, uploaded CSV, database, model or credentials were published. PR remains Draft and must not merge until R1's end-to-end chain is complete.
