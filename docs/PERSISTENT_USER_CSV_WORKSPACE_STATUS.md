# Persistent user CSV workspace — implementation checkpoint

Status: **partial implementation / not yet wired into training and evaluation**.

## User decision

Training and test CSV files use a persistent, independent user workspace. Data survives application restart and is removed only through an explicit user delete action. Historical telemetry DB must not receive these CSV imports.

## Source added in this branch

- `AAS/scripts/server/v2_user_csv_workspace.py`
- `AAS/tests/test_v2_user_csv_workspace.py`

Storage location: `datasets/user_csv_workspaces/user_csv_workspace.sqlite`.

The workspace stores original uploaded bytes, SHA-256, filename, role (train/test), metadata and creation time. A task-reference table prevents deletion while a task is bound to a dataset. No automatic expiry or cleanup is implemented. The module does not open or mutate `datasets/aas_telemetry_v2.sqlite`.

## Verification

Local isolated tests: **4 passed**.
- Persistent import/list/read.
- Reject unconfirmed imports and invalid roles.
- Task references block deletion until released.
- Historical database bytes remain unchanged.

Python compilation passed.

## Explicit remaining work — do not treat as complete

The storage module is not yet connected to the HTTP CSV import routes, runner UI, task creation/recovery, algorithm subprocess supervisor, model provenance, or evaluation loaders. Existing training/evaluation still use the active V2 database in the current candidate. The end-to-end training → model publish → evaluation flow from this workspace has **not** been implemented or tested.

Next:
1. Add authenticated list/import/get/delete APIs, reusing existing CSV preflight/format confirmation.
2. Wire runner UI source selector and dataset manager.
3. Convert/import selected CSV into a task-scoped V2-compatible SQLite database without writing to historical DB; preserve unknown labels as SQL NULL and enforce train/test provenance.
4. Bind workspace dataset IDs and database snapshots to persistent tasks; release task references only after terminal states and retention policy permits.
5. Route the exact snapshot path through the subprocess supervisor and loaders; persist provenance in model/report.
6. Add real HTTP and end-to-end training/evaluation tests; verify restart persistence and historical DB hash/count unchanged.

This repository is Public. The user explicitly authorized publishing the source files in this feature branch. Do not publish real user CSVs, databases, models, credentials, local configuration, or private telemetry.
