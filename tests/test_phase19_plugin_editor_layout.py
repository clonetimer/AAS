"""Phase 19 自定义插件安装工作区布局：仅改布局，不改安装契约。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'web' / 'visualization' / 'plugins.html').read_text(encoding='utf-8')
JS = (ROOT / 'web' / 'visualization' / 'plugins.js').read_text(encoding='utf-8')


def test_editor_layout_has_both_panels_and_no_duplicate_ids():
    assert 'class="pg-install-config"' in HTML
    assert 'class="pg-install-editor"' in HTML
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    assert len(ids) == len(set(ids))
    assert 'grid-template-columns:minmax(300px, .78fr) minmax(0, 1.45fr)' in HTML
    assert '@media (max-width:1100px)' in HTML


def test_all_existing_install_fields_are_preserved_in_correct_panel():
    left = HTML.split('class="pg-install-config"',1)[1].split('class="pg-install-editor"',1)[0]
    right = HTML.split('class="pg-install-editor"',1)[1].split('</form>',1)[0]
    for field in ('pluginType','implementationLanguage','restoreTemplateBtn','clearTemplateBtn','yamlFileInput','codeFileInput','libraryFileInput','validatePluginBtn','codeRequirement'):
        assert f'id="{field}"' in left
    for field in ('yamlName','yamlContent','codeName','codeContent','nativeBuildBox','pluginValidation','installBtn','installMsg'):
        assert f'id="{field}"' in right
    assert '<form id="installForm" class="pg-form">' in HTML
    assert "form.addEventListener('submit'" in JS
    assert "fetch('/api/plugins/install'" in JS
    assert "fetch('/api/plugins/preview'" in JS


def test_code_editors_have_readable_minimum_heights():
    assert '.pg-install-editor #yamlContent { min-height:180px; }' in HTML
    assert '.pg-install-editor #codeContent { min-height:300px; }' in HTML
    assert 'resize:vertical' in HTML
