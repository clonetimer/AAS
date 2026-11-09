# P3-A CSV Import Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify CSV encoding and delimiter handling across simulation workspace and historical telemetry import without changing the V2 data/label safety contract.

**Architecture:** Extract bounded CSV decoding, GZIP handling, delimiter detection and normalization into `scripts/server/csv_format.py`. Keep the existing historical source importer API stable, and adapt the simulation workspace to the same parser. Both browser flows explicitly confirm detected format before preview/commit and submit original bytes plus a small metadata header.

**Tech Stack:** Python 3.11+, stdlib `csv`/`gzip`/`zlib`/`sqlite3`, existing HTTP handler, browser JavaScript, pytest, Node.js.

**Spec:** `docs/superpowers/specs/2026-11-06-p3-csv-import-consistency-design.md`

## Global Constraints

- Simulation workspace remains isolated in `datasets/aas_simulation_v2.sqlite` and never mutates `aas_telemetry_v2.sqlite`.
- Simulation CSV upload remains at most 8 MB, 50,000 frames and 12 MB decompressed.
- Historical source import remains at most 64 MB upload and 12 MB decompressed.
- CSV encoding options are `utf-8` and `gb18030`; delimiters are comma, semicolon and TAB.
- Format must be explicitly confirmed before preview/import; changed file or format invalidates previous approval.
- SHA-256 is computed from original uploaded bytes, not normalized text.
- Existing labels are preserved; missing labels are never inferred as normal.

## Review Focus

- GB18030 Chinese text containing quoted delimiters remains intact; test exact decoded values.
- UTF-8 BOM and TAB delimiters are parsed identically by inspect, preview and commit.
- GZIP bombs, trailing bytes and malformed CSV are rejected before any DB mutation.
- A format change or stale in-flight inspection cannot authorize import of different bytes/options.
- The historical-record “continue injection” path still imports its internally generated UTF-8 CSV with explicit format metadata.

---

### Task 1: Shared CSV format primitives

**Files:**
- Create: `scripts/server/csv_format.py`
- Modify: `scripts/server/telemetry_v2_source_import.py`
- Test: `tests/test_v2_source_file_import_phase33.py`

**Interfaces:**
- Produces `decode_csv(raw, encoding)`, `decompress_gzip(raw, max_output_bytes)`, `detect_delimiter(text)`, `normalize_delimiter(text, delimiter, max_rows, max_bytes)`, and `resolve_csv_format(raw, encoding, delimiter, max_output_bytes)`.
- Existing private helper names in `telemetry_v2_source_import.py` remain compatibility wrappers.

- [x] Add failing tests for GB18030/semicolon, UTF-8 BOM/TAB, quoted delimiters and GZIP size/trailing-data rejection through existing `source_operation`.
- [x] Run the focused tests and verify failures are due to missing shared behavior, not fixture errors.
- [x] Implement the shared primitives and keep old helper behavior/signatures.
- [x] Run `pytest -q tests/test_v2_source_file_import_phase33.py`.
- [x] Commit `refactor: share bounded csv format parsing`.

### Task 2: Simulation workspace parser and format provenance

**Files:**
- Modify: `scripts/server/v2_csv_workspace.py`
- Modify: `scripts/server/handler_post.py`
- Test: `tests/test_v2_csv_workspace_upgrade.py`

**Interfaces:**
- `inspect_csv(raw, filename, *, csv_encoding='auto', csv_delimiter='auto') -> dict` returns resolved format and `format_review_required`.
- `import_csv(root, raw, filename, *, csv_encoding, csv_delimiter, format_confirmed) -> dict` rejects implicit/auto format or missing confirmation.
- HTTP format metadata header: `X-AAS-CSV-Format-B64`, JSON allowlist `{csv_encoding, csv_delimiter, format_confirmed}`.

- [x] Add failing tests for GB18030/semicolon, UTF-8 BOM/TAB, GZIP GB18030/TAB, missing confirmation, auto format on import and original SHA preservation.
- [x] Run the new tests and verify they fail for the current UTF-8/comma-only parser.
- [x] Implement format-aware parse, explicit confirmation and persisted format metadata; migrate existing simulation tables idempotently.
- [x] Update the binary HTTP route to validate and pass the format metadata.
- [x] Run `pytest -q tests/test_v2_csv_workspace_upgrade.py` and `pytest -q tests/test_v2_source_file_import_phase33.py`.
- [x] Commit `feat: add confirmed csv formats to simulation workspace`.

### Task 3: Simulation CSV browser workflow

**Files:**
- Modify: `web/visualization/simulation.html`
- Modify: `web/visualization/simulation-csv-workspace.js`
- Modify: `web/visualization/simulation-v2.js`
- Test: `tests/csv_workspace_ui.cjs`

**Interfaces:**
- Browser sends original `File` bytes as `application/octet-stream`, filename in `X-AAS-CSV-Filename-B64`, and format JSON in `X-AAS-CSV-Format-B64`.
- The history-record “continue injection” path sends explicit `{csv_encoding:'utf-8', csv_delimiter:',', format_confirmed:true}`.

- [x] Add failing interaction tests for auto-detection, manual format re-inspection, mandatory confirmation and invalidation after file/format changes.
- [x] Run `node tests/csv_workspace_ui.cjs` and verify the new assertions fail against the current UI.
- [x] Add format controls, CSV.GZ acceptance, explicit confirm gating and metadata headers.
- [x] Run the simulation UI test and `node --check` for both simulation scripts.
- [x] Commit `feat: confirm csv format in simulation import ui`.

### Task 4: Historical database import uses the same binary parser

**Files:**
- Modify: `web/visualization/import.html`
- Modify: `web/visualization/telemetry-v2-import.js`
- Test: `tests/test_v2_source_file_import_phase33.py`
- Create: `tests/telemetry_v2_import_format_ui.cjs`

**Interfaces:**
- `.csv`, `.csv.gz` and SQLite all use `/api/v2/telemetry/import-source`; CSV/CSV.GZ metadata includes `csv_encoding`, `csv_delimiter`, and `format_confirmed`.
- Plain CSV uses the existing binary endpoint, avoiding JSON text re-encoding and browser-default UTF-8 parsing.

- [x] Add failing browser contract tests for GB18030/semicolon inspection, explicit confirmation, format change invalidation and stable preview/commit metadata.
- [x] Run the new Node test and verify it fails against the current page/script behavior.
- [x] Route plain CSV through `binaryApi`, add encoding/delimiter/confirm controls and explicit re-inspection after overrides.
- [x] Preserve SQLite selection, CSV.GZ, upload progress/cancel and existing label/source checks.
- [x] Run the new UI test and all source import tests.
- [x] Commit `feat: unify historical csv import format review`.

### Task 5: Cross-flow regression and delivery notes

**Files:**
- Modify: `docs/UPGRADE_STATUS_AND_NEXT_STEPS_20261105.md`
- Modify: `docs/UPGRADE_DELIVERY_PACKAGE.md`
- Test: `tests/test_v2_csv_workspace_upgrade.py`, `tests/test_v2_source_file_import_phase33.py`, `tests/csv_workspace_ui.cjs`, `tests/telemetry_v2_import_format_ui.cjs`, `tests/runner_csv_format_behavior.cjs`

- [x] Run all P3-A backend tests and browser scripts.
- [x] Run `python -m compileall -q scripts` and `node --check` on changed scripts.
- [x] Verify existing simulation DB migration with a fixture created using the pre-change schema.
- [x] Verify ZIP contains no database, model, secret or runtime output; regenerate manifest and source ZIP.
- [x] Record all failures/skips honestly; do not label Windows Stable without target-machine GUI acceptance.
- [x] Commit `docs: record unified csv import acceptance`.

## Next work after P3-A

- P3-B: data quality summary, row-level error location, column role controls/presets and recent mappings.
- P3-C: side-by-side experiment comparison, comparable-condition explanations, and actionable failure-stage reasons. Existing comparison API/UI is retained and will be audited before extending.
- P3-D: task-based large-file import, progress, cancel/retry and paginated preview.
