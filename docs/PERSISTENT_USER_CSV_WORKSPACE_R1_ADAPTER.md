## G0-3-R1 implementation update — isolated V2 source adapter

This is a source-level increment on the existing Draft PR. The original foundation stores uploaded bytes and task references in `user_csv_workspace.sqlite`. This commit adds an adapter that materializes a confirmed CSV commit into the same isolated SQLite file using the existing V2 wide-source importer.

### Implemented
- `AAS/scripts/server/v2_user_csv_adapter.py`
- Validates immutable dataset role (train/test), explicit format confirmation, operation=commit, filename identity and role-specific label confirmations.
- Calls `source_operation_file()` with `workspace.database_path(root)`, never `active_path(root)`.
- Preserves the original uploaded bytes and SHA-256; stores the resulting V2 `source_id` and import summary in the persistent dataset record.
- Import failure leaves the original uploaded file available for correction/retry.
- Adds adapter tests for workspace DB destination, history DB byte preservation, role mismatch rejection and explicit format confirmation.

### Verification
- Local tests: `pytest -q tests/test_v2_user_csv_workspace.py tests/test_v2_user_csv_adapter.py` — **7 passed**.
- `python -m py_compile scripts/server/v2_user_csv_adapter.py` — PASS.
- The test uses a controlled importer stub to prove target path and data-boundary behavior; it does **not** yet prove actual wide CSV importer end-to-end.

### Remaining R1 work (not claimed complete)
- Authenticated HTTP inspect/preview/commit/list/get/delete routes.
- Runner UI switches to the user workspace endpoint.
- Training/evaluation selection and algorithm subprocess use a task-scoped workspace database path.
- Task reference binding/release across restart and terminal states.
- Real importer integration, train→publish→evaluate and score-only end-to-end tests.
- Model/report provenance includes workspace dataset ID and source SHA-256.

### R2–R4
Simulation CSV ownership/version chain, isolation/restart tests and first-use UX acceptance remain outstanding. The PR remains Draft; do not merge until the end-to-end chain is verified.