# AAS V2 Score-Only Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checklist syntax for tracking.

**Goal:** Imported unlabeled V2 test CSV can be scored with a previously published compatible detector, with results shown and archived, without inventing labels or supervised metrics.

**Architecture:** Separate score-only data selection and execution route; share catalog and model schema/scaler primitives but preserve supervised evaluation code. Keep inference inside bounded existing algorithm subprocess. Render in existing evaluation workspace.

**Tech Stack:** Python 3.11+, SQLite V2, pytest, plain JS/Node and PowerShell 5.1.

**Spec:** `docs/superpowers/specs/2026-11-05-aas-score-only-inference-design.md`

## Global Constraints

- One imported test source per score-only job, strictly all NULL labels; no metrics; no fit; model and scaler from saved published artifact.
- No SQLite or model writes by scoring; preserve exact feature order and semantic identity and train/test leakage rules.
- P0–P3 existing behaviors and Windows candidate packaging must remain intact; no claim of Stable.

## Review Focus

- Mixed labeled/unknown rows: reject from score-only; add regression test in Task 1.
- Same file digest or group reused across training/test: reject; add regression test in Task 1.
- Feature order/physical unit mismatch: reject; add regression test in Task 1.
- Unbounded sample count/nonfinite numeric values: bound and reject or account; add test in Task 1.
- Worker returns zero/misaligned score count or model tries to fit: reject; add test in Task 2.

---

### Task 1: Discover and align unknown-label source with saved feature schema

**Files:** Modify `scripts/server/v2_run_dataset.py`; create `scripts/server/v2_score_only.py`; test `tests/test_v2_score_only_upgrade.py`.

**Interfaces:** `catalog(db_path)['runs']` now may contain `allowed_role='score_only'`; `load_score_only_rows(db_path, *, selection, expected) -> (rows, contract)` where expected includes feature keys, train IDs/groups/digests and stored feature schema.

- [ ] Write failing tests: unlabeled direct CSV appears as score_only (never test/train); saved order and correct aligned rows, unknown label not converted; groups/digests and changed units rejected; mixed labels and invalid times rejected.
- [ ] Run targeted pytest and confirm RED failure caused by absent score-only classification/loader.
- [ ] Implement qualified source discovery and bounded read-only aligned sample loader; reuse V2 time selection/scoring primitives.
- [ ] Re-run tests GREEN plus existing source import/label target tests.
- [ ] Commit Task 1.

### Task 2: Isolated model inference, result contract, persisted report

**Files:** `scripts/server/v2_score_only.py`, `scripts/server/algorithm_process.py`, `scripts/server/algorithm_worker.py`, `scripts/server/telemetry_v2_routes.py`, `scripts/server/handler_post.py`; tests `tests/test_v2_score_only_upgrade.py`.

**Interfaces:** `AlgorithmProcessService.run_score_only(...) -> summary`; `score_with_published_detector(...) -> summary`; POST `/api/v2/inference/run` with `{model_id,dataset_selection}`, response `evaluation_mode='score_only'`, `metrics={}`, `ground_truth_available=false`, server report archive.

- [ ] Write failing tests: preloaded detector scores without fit; saved scaler reused and labels omitted, mismatched scores rejected; unauthenticated/invalid model selection and route rejected, report persisted without metrics.
- [ ] Run RED, implement smallest safe subprocess action and bound server route reusing existing trusted model validation and execution registry.
- [ ] Run GREEN plus P0 report/model bundle/phase41 process tests; commit Task 2.

### Task 3: Non-programmer UI, regression and candidate package

**Files:** `web/visualization/runner-v2.js`, `web/visualization/runner.html`, `web/visualization/runner-csv-import.js` as needed, `LOCAL_TEST/Run-UpgradeTests.ps1`, docs, `_pack_code.py` outputs; tests JS/pytest.

**Interfaces:** Existing evaluation page automatically chooses score-only for `allowed_role='score_only'` after import; existing labeled flow remains unchanged. Result graph without unverified truth coloration.

- [ ] Write failing UI contract regression and verify RED; add selection routing, explicit mode text/score-only chart, and correct imported-file selection.
- [ ] Verify GREEN, new and prior test suite, JS syntax, compileall, run attempted full old pytest and report known failures.
- [ ] Update documents and create new versioned candidate ZIP with manifest; verify and upload only after fresh test pass; commit Task 3.

## Execution

User repeatedly requested direct continuation of the agreed AAS upgrade roadmap. Continue inline on existing feature branch, with TDD and per-task commits; keep the original package untouched and avoid main merge until full acceptance.
