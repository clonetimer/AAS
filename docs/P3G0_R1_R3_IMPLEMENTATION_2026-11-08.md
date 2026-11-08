# AAS P3-G0 R1–R3 implementation checkpoint — 2026-11-08

## Source identity
- Base: verified P3-G0-3 v2 candidate ZIP, SHA-256 `8afc857184e82de217e631453d74d545c1571b15b3da4fe93b538cda9a3f33de`.
- Local Git baseline: `c41acec` (fresh import of the source ZIP; ZIP contained no .git metadata).
- Feature branch: `feat/aas-r1r3-complete`.
- Implementation commit: `30580a5` — `feat: complete isolated user CSV training and evaluation flow`.
- Full-source candidate ZIP: `AAS_V2_P3G0_R1-R3_20261108_candidate.zip`.
- ZIP bytes: 2,208,628; entries: 672; manifest files: 671; ZIP CRC: PASS; all manifest SHA-256 entries: PASS.
- ZIP SHA-256: `dc9d6c5cf8e5f4438887faefa2d68284a1e98f6897c0ee19a91e380fd004abff`.

## R1 — user-owned persistent training/test workspace

### Implemented
- Independent V2 SQLite DB: `datasets/user_csv_workspaces/user_csv_workspace.sqlite`.
- Initialization creates the full V2 logical-source schema plus the workspace registry; existing metadata-only workspace DBs are upgraded additively, incompatible schemas are rejected rather than destructively recreated.
- Authenticated endpoints:
  - `POST /api/v2/user-csv-workspace/import-source` — inspect/preview/commit, with task progress/cancellation for async commit.
  - `GET /api/v2/user-csv-workspace/list?role=train|test`
  - `GET /api/v2/user-csv-workspace/dataset?dataset_id=...`
  - `POST /api/v2/user-csv-workspace/delete` — explicit confirm, blocks active references and in-progress imports.
- Existing V2 wide-source importer commits directly into the user workspace DB, not the active history DB.
- Runner UI has a data source selector (history vs. user CSV workspace), role-scoped dataset list, import action and deletion management.
- Training, supervised evaluation and score-only inference resolve a server-owned database reference; the browser cannot submit an arbitrary filesystem path.
- Algorithm worker allows only the configured history DB or the fixed user-workspace DB path, rejects symlinks/path traversal and passes the selected DB into the isolated child process.
- Model data contract, task result and execution provenance include source type, workspace dataset ID, filename, role and source SHA-256.
- Task references prevent deleting datasets in use. References are released on terminal task completion; restart reconciliation releases only when a durable terminal state is known. Unknown/unavailable task status fails closed.
- Import uses idempotency key + namespace/digest recovery for the crash window between V2 source commit and workspace registry update. Unknown task status does not permit a duplicate retry.

### End-to-end evidence
- Real CSV import into isolated workspace.
- V2 catalog and run selection read the isolated DB.
- Real route-level training task completes.
- Trained model is published through the model registry and retains workspace provenance.
- Separate labeled test CSV is evaluated using the published model.
- Separate unlabeled CSV executes score-only inference; unknown labels remain SQL NULL and no supervised metrics are emitted.
- Historical DB sentinel SHA-256 is unchanged throughout the train → publish → supervised evaluate → score-only flow.

## R2 — simulation CSV workspace
- Retained existing independent `aas_simulation_v2.sqlite` implementation; did not route simulation CSV to history or user train/test DB.
- Original CSV is immutable v0; sequential fault injection creates child snapshots with parent/version metadata and event ledger.
- Rollback creates a new child version; prior versions remain unchanged.
- Fixed deletion path to use the module's actual SQLite connection contract.
- Explicit lineage deletion removes only the selected root lineage and leaves unrelated imports intact.

## R3 — isolation/restart checks
- User CSV database initialization and history DB byte/hash isolation.
- Actual V2 importer writes only to workspace DB; inspect/preview do not commit V2 sources.
- Authenticated HTTP import/list/catalog/delete flows.
- Task references survive fresh DB reopen; active references prevent deletion; terminal/interrupted task state permits release; unknown state retains protection.
- In-progress CSV import cannot be deleted.
- Algorithm worker DB path allowlist and symlink/path traversal rejection.
- Simulation: original import → bias injection → stuck injection → reopen → version/event chain → rollback as new version; history DB hash unchanged.

## Test results
### Focused Python
`pytest -q tests/test_v2_user_csv_workspace.py tests/test_v2_user_csv_workspace_http.py tests/test_v2_user_csv_workspace_real_import.py tests/test_v2_user_csv_workspace_routes_e2e.py tests/test_v2_user_csv_workspace_restart.py tests/test_algorithm_worker_workspace_db.py tests/test_v2_simulation_workspace_r1r3.py`
- **22 passed**.

### Combined R1–R3 regression
- **63 passed, 2 failed, 3 skipped**.
- The two failures are known stale/environment baseline contracts in `tests/test_simulation_database_lifecycle.py`:
  1. Old UI assertion expects `deleteSimBtn` in the legacy historical simulation UI; the current UI uses the newer CSV workspace / separate historical mode.
  2. Lifecycle startup test requires `datasets/source_labels/targets.zip`, which is not included in this source candidate.
- They are disclosed, not hidden or counted as passes. No evidence they are caused by R1 changes.
- One earlier run exposed a catalog request bug and a simulation deletion connection-name bug; both were fixed and the focused/combined suites rerun.

### Frontend and static checks
- `runner_csv_async_task_ui.cjs`: PASS.
- `runner_csv_format_behavior.cjs`: PASS.
- `runner_csv_import_ui.cjs`: PASS.
- `runner_score_only_ui.cjs`: PASS.
- `csv_workspace_ui.cjs`: PASS.
- `node --check` for modified runner JS: PASS.
- `python -m compileall -q scripts`: PASS.
- `git diff --check`: PASS.

## Remaining release boundary
- Linux local HTTP/route and algorithm end-to-end tests pass for the selected fixture; real browser visual acceptance and Windows 10/11 fresh-machine acceptance are not done.
- Full project suite is not claimed green; combined R1–R3 suite retains the two known failures above.
- PR remains Draft; no merge to main.
- The repository is Public; only source code, tests and sanitized docs are published. No user CSV, production database, model artifacts, credentials or private telemetry are included.
