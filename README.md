# AAS V2

AAS V2 is the telemetry fault-detection algorithm evaluation system.

## Development branches

- P3-D: `feat/aas-p3d-long-task-runtime` — unified task lifecycle, cancellable algorithm processes, async evaluation/inference, streaming import and shared task UI.
- P3-E: `feat/aas-p3e-history-browser` — SQL catalog pagination, bounded chart overview, server-backed zoom, single-channel cursor details and full 50,000-frame traversal.

## Current reports

- P3-D baseline and architecture: `docs/P3D_BASELINE_2026-11-07.md`, `docs/P3D_DESIGN.md`, `docs/P3D_PLAN.md`.
- P3-D progress: `docs/P3D_PROGRESS_2026-11-07.md`.
- P3-E final progress: `docs/P3E_PROGRESS_2026-11-07.md`.
- P3-E cursor performance root cause/fix: `docs/P3E_CURSOR_PERFORMANCE_2026-11-07.md`.
- P3-E Task 5: `docs/P3E_TASK5_PROGRESS.md`.

## Patch archive

- `patches/P3E_TASK5_AND_CURSOR_FIX.patch.gz` is the compressed Git patch for P3-E Task 5 and the cursor performance correction.
- Its SHA-256 is recorded in `docs/P3E_PROGRESS_2026-11-07.md`.

## Source completeness

This repository currently stores development reports and reproducible patch artifacts. It does **not** yet contain the complete AAS application source tree and must not be treated as a complete cloneable/deployable application. The full P3-E candidate source archive is delivered separately. The P3-C source baseline and all candidate archive hashes are recorded in the progress documents.

## Workflow

Changes are isolated on feature branches, verified against the P3-C baseline, recorded with commit IDs and SHA-256 hashes, and merged only after review and regression verification. Linux tests do not replace Windows 10/11 acceptance; that remains P3-H.
