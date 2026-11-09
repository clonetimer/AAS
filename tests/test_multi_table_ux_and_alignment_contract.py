"""UI and API input contract guard against accidentally reintroducing silent joins."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'web' / 'visualization'


def test_browser_table_picker_explains_scope_and_allows_explicit_primary():
    page = (VIEW / 'browser.html').read_text(encoding='utf-8')
    code = (VIEW / 'browser.js').read_text(encoding='utf-8')
    for element in ('primaryTableSelect', 'selectedTablesCount', 'resetBrowserFiltersBtn'):
        assert f'id="{element}"' in page
        assert element in code
    assert '主数据表' in page
    assert '图表显示全部选中表' in page
    assert 'picker-tools' in code and '选中可见' in code and '清空已选' in code
    assert 'state.tableSearch' in code


def test_training_has_explicit_time_tolerance_and_quality_gate():
    page = (VIEW / 'runner.html').read_text(encoding='utf-8')
    code = (VIEW / 'runner.js').read_text(encoding='utf-8')
    for element in ('trainTablesSearch', 'evalTablesSearch', 'alignmentTolerance',
                    'checkAlignmentBtn', 'trainAlignmentPreview'):
        assert f'id="{element}"' in page
        assert element in code
    assert '<option value="5" selected>' in page
    assert 'id="alignmentMethod"' in page
    assert 'alignmentToleranceSeconds' in code
    assert 'const preview = await refreshAlignmentPreview()' in code
    assert 'window.confirm' in code
    assert '最大偏差' in code


def test_training_and_evaluation_use_same_persisted_policy():
    server = (ROOT / 'scripts/visualization_server.py').read_text(encoding='utf-8')
    handlers = (ROOT / 'scripts/server/handlers.py').read_text(encoding='utf-8')
    loader = (ROOT / 'scripts/core/data_loader.py').read_text(encoding='utf-8')
    assert '"alignment_tolerance_seconds": float(alignment_tolerance_seconds)' in server
    assert 'alignment_tolerance_seconds=payload.get("alignmentToleranceSeconds", 0)' in handlers
    assert 'alignment_tolerance_seconds=data_contract.get("alignment_tolerance_seconds", 0)' in handlers
    assert 'tolerance_seconds=getattr(self, "alignment_tolerance_seconds", 0)' in loader
