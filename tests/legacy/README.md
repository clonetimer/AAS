# tests/legacy — 门禁外的过期用例

这里的用例**不在** `tests/v2_stable_gate.json` 的 232 个稳定节点内，也不参与交付门禁判定。
保留它们是为了留档"旧模块布局曾经被这样断言过"，而不是为了让它们通过。

## 为什么要单独放

它们在源码结构演进后已经**断言的是历史事实**，改前即失败：

| 文件 | 过期原因 |
|---|---|
| `check_phase14_formal_ux.py` | 断言 `import.html` 里存在旧文案「列属性映射文件」，导入向导改版后已不存在 |
| `check_phase18_rename_and_fault_type_v2.py` | 断言 `/api/v2/simulation/fault-types` 写在 `scripts/server/handlers.py`，该路由已拆到 `handler_get.py` |
| `check_phase22_task_resources.py` | 断言 `task_resources=` 出现在 `handlers.py`（已拆模块）；另一用例依赖本机不存在的 `outputs/experiment_9abf0ae04e80` |
| `check_browser_multifeature_ui_contract.py` | 断言 V1 契约：`browser.html?table_series=2`、`browser.js?unified=1`、`multi_modelFilter` 等，V2 前端已无这些标记 |

放在 `tests/` 顶层会让 `python -m pytest tests/` 直接变红，把"历史遗留"误读成"当前回归"。
改名去掉 `test_` 前缀后，默认收集规则（`python_files = test_*.py`）不会递归到它们。

## 现状（必须知道，别被 `pytest tests/` 的红字误导）

把本目录 4 个文件移出后，`python -m pytest tests/` **仍然不是全绿**：
2026-10-25 实测 `131 failed, 604 passed, 11 skipped, 19 errors`。

这些红点**不是本轮改动引入的**，而是长期存在的 V1 遗留套件（多数在断言已退役接口返回 200，
例如 `/api/export-tables/list`、`/api/fault-intervals` 的 CRUD、`/api/sqlite-*`、原生运行时隔离等），
它们从未进入交付门禁的 232 个稳定节点。已用改动前后对照实测：删除死路由**没有增加**失败
（同一批文件：改动前 `35 failed / 14 errors`，改动后 `34 failed / 12 errors`）。

**判定口径**：交付门禁以 `scripts/check_v2_stable.py`（`tests/v2_stable_gate.json` 冻结的 232 节点）
为准，不以 `pytest tests/` 的整体红绿为准。遗留套件需要单独做一次"退役 / 重写 / 删除"分诊，
不在整改范围内。

## 怎么跑

需要对照时显式指定收集规则：

```powershell
cd D:\AAS
.\.venv\Scripts\python.exe -m pytest -o python_files="check_*.py" tests/legacy/ -q
```

## 处理原则

- **不要**为了"变绿"而放宽断言去迎合旧实现；要修就修成对当前契约的断言，并放回 `tests/`。
- 若某个断言仍然代表有效的业务约束（例如"未登录不可访问写接口"），应改写而不是丢弃；
  已按此原则把 `tests/test_home_login_and_dashboard.py` 修回当前契约（`/api/databases` 现在是 410）。
- 新增用例请直接放 `tests/`，并在 `tests/v2_stable_gate.json` 里登记节点后重跑门禁。
