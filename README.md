# AAS V2

AAS V2 is the repository for the satellite/aircraft telemetry fault-detection algorithm evaluation system.

## Current development

- P3-C source baseline: candidate ZIP dated 2026-11-06; SHA-256 is recorded in docs/P3D_BASELINE_2026-11-07.md.
- P3-D design and roadmap: docs/P3D_DESIGN.md and docs/P3D_PLAN.md.
- P3-D baseline and known test failures: docs/P3D_BASELINE_2026-11-07.md.
- P3-D Task 1 progress and test evidence: docs/P3D_TASK1_PROGRESS.md.
- Active development branch: feat/aas-p3d-long-task-runtime.

## Source completeness

The initial repository was created separately from the P3-C source archive. The feature branch currently stores design, plans, baseline evidence and reproducible patches; it does not yet contain the complete AAS source tree. Do not treat this repository as a complete cloneable application until the baseline source tree has been imported and verified.

## Workflow

Changes are developed on feature branches, tested against the local P3-C baseline, recorded with commit IDs and SHA-256 hashes, and merged only after review and regression verification. Windows 10/11 acceptance must be performed on Windows and is not inferred from Linux tests.
