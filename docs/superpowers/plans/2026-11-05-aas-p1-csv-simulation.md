# P1-A: CSV 工作区、帧区间与可复现的多次故障注入

> Use executing-plans task start/done, TDD RED→GREEN, commit each task; do not modify the historical V2 main database.

## Task 1: 严格 CSV 导入与独立工作区存储

- Create `scripts/server/v2_csv_workspace.py`.
- Test `tests/test_v2_csv_workspace_upgrade.py`: RFC4180, UTF-8 BOM, 1-based frame, no label inference, bounds, rejected empty/malformed data, immutable source, no writes into history DB.
- API later uses raw bytes with explicit size and filename checks.
- Tests GREEN; commit.

## Task 2: 帧区间预览、多次注入及不变版本链

- Extend `scripts/server/v2_csv_workspace.py`.
- Tests: inject first bias 2-4 then second stuck 3-5 over version-1; original and first version unchanged; inclusive frame semantics; invalid intervals/columns; conflict sha256; event history and true source labels never overwritten.
- Tests GREEN; commit.

## Task 3: 全量 CSV 导出与 HTTP 路由

- Extend `scripts/server/v2_csv_workspace.py` and `handler_post.py`, `handler_get.py`.
- Tests: full frame count/export columns/retained original labels; safe spreadsheet output and exact source CSV values on roundtrip; auth HTTP import → preview → save → export; page refresh list.
- Tests GREEN; commit.

## Task 4: 前端 CSV 工作区交互、回归说明

- Add `web/visualization/simulation-csv-workspace.js` and simulation UI tabs; use `File` uploads, preview and version pick, 1-based frame interval, full export link, distinct status and validation messages.
- Add JS DOM contract tests; run Python+Node regression, `git diff --check`, produce `docs/UPGRADE_P1_CSV_SIMULATION.md` including limitations.
- Tests GREEN; commit.

## Scope note

This adds a parallel self-contained multi-channel CSV workflow inside the V2 simulation database (one logical dataset and frames table, no per-fault physical table), while preserving existing historical V2 fault records. CSV training/model import and relaxing history catalog restrictions follow separately and must not be silently claimed implemented by this phase.
