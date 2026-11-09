"""模拟故障数据库：已保存记录必须可筛选、可定位到来源参量。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _page() -> tuple[str, str]:
    html = (ROOT / 'web/visualization/simulation.html').read_text(encoding='utf-8')
    js = (ROOT / 'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    return html, js


def test_record_table_exposes_filter_controls():
    html, _ = _page()
    for name in ('simFilterText', 'simFilterType', 'simFilterColumn', 'simFilterSort',
                 'simFilterReset', 'simFilterCount'):
        assert f'id="{name}"' in html, name
    # 记录表必须能看出是"哪条注入、哪一列参量"
    for header in ('模拟型号', '注入参量', '故障', '起止帧', '幅值', '故障帧数'):
        assert header in html, header


def test_filters_match_text_type_column_and_sort():
    _, js = _page()
    assert 'function visibleRecords' in js and 'function renderRecords' in js
    assert 'row.source_column!==f.column' in js          # 参量精确筛选
    assert 'row.fault_type!==f.type' in js               # 故障类型筛选
    assert 'row.source_token' in js and 'toLowerCase()' in js  # 关键字可搜来源 token
    assert 'onset_desc' in js and 'id_asc' in js         # 排序
    assert '没有符合当前筛选条件的记录' in js              # 空结果有明确提示


def test_empty_state_spans_every_column():
    _, js = _page()
    assert 'td.colSpan=8' in js


def test_saved_record_row_carries_injection_identity():
    _, js = _page()
    # #ID / 型号 / 参量 / 故障 / 起止帧 / 幅值 / 影响帧数 七列数据 + 操作列
    assert '`#${row.id}`' in js
    assert 'row.source_column' in js and 'row.magnitude' in js and 'row.injected_rows' in js
    assert '共读取' in js and 'row.sampled_rows' in js
