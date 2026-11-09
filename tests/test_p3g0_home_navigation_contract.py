from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_home_has_one_entry_for_historical_telemetry_database():
    html = (ROOT / 'web/visualization/home.html').read_text(encoding='utf-8')
    cards = re.findall(r'<a\b[^>]*class="module-card"[^>]*>(.*?)</a>', html, re.S)
    historical = [card for card in cards if '历史故障数据库' in card]
    telemetry_alias = [card for card in cards if re.search(r'<strong>\s*遥测数据\s*</strong>', card)]
    assert len(historical) == 1
    assert not telemetry_alias


def test_workbench_navigation_uses_task_oriented_groups():
    js = (ROOT / 'web/visualization/workbench.js').read_text(encoding='utf-8')
    for label in ('数据准备', '算法评估', '结果与管理'):
        assert label in js
    assert '功能模块' not in js


def test_home_task_summary_reads_persistent_task_history():
    js = (ROOT / 'web/visualization/home.js').read_text(encoding='utf-8')
    assert '/api/task-history?limit=100&include_all=1' in js
    assert '本次服务已完成' not in js
    assert '任务历史暂不可用' in js


def test_home_explains_task_status_scope_and_terminal_states():
    html = (ROOT / 'web/visualization/home.html').read_text(encoding='utf-8')
    for label in ('已取消', '服务重启中断', '失败', '已完成', '运行中'):
        assert label in html
