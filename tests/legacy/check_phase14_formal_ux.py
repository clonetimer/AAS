"""Phase 14 formal UI contracts. Browser smoke is separately exercised with Chromium."""
from pathlib import Path

WEB=Path(__file__).resolve().parents[1]/'web'/'visualization'

def txt(name):return (WEB/name).read_text(encoding='utf-8')

def test_navigation_has_no_legacy_import_or_governance_page():
    shell=txt('workbench.js')
    assert "key: 'import'" not in shell
    assert "key: 'governance'" not in shell
    assert "key: 'simulation'" in shell
    assert not (WEB/'governance.html').exists()

def test_browser_import_modal_and_zoom_brush():
    html=txt('browser.html');js=txt('telemetry-v2-browser.js')
    for anchor in ('v2OpenImport','v2ImportDialog','v2ImportFrame','v2ZoomIn','v2ZoomOut','v2ZoomReset','v2ZoomStatus'):
        assert anchor in html
    for required in ('v2-brush-selection','pointermove','pointercancel','updateZoomControls','timeValue(point,domain)','setPointerCapture'):
        assert required in js
    assert 'state.writeDatabase' in js

def test_import_uploads_mapping_csv_without_common_metadata_fields():
    html=txt('import.html');js=txt('telemetry-v2-import.js')
    assert 'id="v2ImpMapFile"' in html and 'id="v2ImpMapTemplate"' in html
    assert '列属性映射文件' in html and 'mappingFromFile' in js
    for name in ('Namespace','Model','Subsystem','Device','Unit'):
        assert f'type="hidden" id="v2Imp{name}"' in html
    assert '一次导入只支持一个型号与一个独立运行标识' in js

def test_preprocessing_reuses_hierarchical_filters():
    html=txt('preprocess.html');js=txt('telemetry-v2-preprocess.js')
    for field in ('v2PrepModel','v2PrepSubsystem','v2PrepDevice','v2PrepKeyword','v2PrepChannel'):
        assert field in html and field in js
    assert 'filterCatalog' in js and 'series/search?role=' in js

def test_training_layout_has_date_pickers_and_idle_panel():
    html=txt('runner.html');css=txt('runner-v2.css');js=txt('runner-v2.js')
    for id in ('trainStart','trainEnd','evalStart','evalEnd'):
        assert f'id="{id}" type="datetime-local"' in html
    assert 'class="v2-run-grid is-idle"' in html and '.v2-run-grid.is-idle>.v2-run-main' in css
    assert 'showResult' in js and 'evalFeatureSubsystem' in html and 'evalFeatureDevice' in html
    assert 'id="trainAdvancedParams"' in html

def test_plugins_scannable_progress_and_chinese_descriptions():
    html=txt('plugins.html');js=txt('plugins.js')
    for value in ('pluginSearch','pg-items{max-height:245px','pg-ready-track','pg-ready-fill'):
        assert value in html
    for name in ('torch_like_cusum','torch_like_ewma','torch_like_pca_spe','torch_like_usad','torch_like_tranad'):
        assert name in js
    assert '就绪 ' in js and 'pg-ready-copy' in js
