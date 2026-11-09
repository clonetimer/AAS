from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_developer_help_uses_full_width_numbered_layout():
    html = (ROOT / "web" / "visualization" / "plugins.html").read_text(encoding="utf-8")

    assert ".pg-dev-list" in html
    assert "grid-template-columns:repeat(2,minmax(0,1fr));" in html
    assert "width:100%;" in html
    assert ".pg-dev-help .pg-hint { max-width:760px; }" not in html
    assert "counter-reset:dev-help;" in html
    assert "content:counter(dev-help);" in html


def test_developer_help_is_split_into_three_topics():
    html = (ROOT / "web" / "visualization" / "plugins.html").read_text(encoding="utf-8")
    block = re.search(r'<ol class="pg-dev-list">(.*?)</ol>', html, re.S)
    assert block is not None
    items = re.findall(r"<li>(.*?)</li>", block.group(1), re.S)

    assert len(items) == 3
    assert "Python 插件" in items[0]
    assert "C/C++ 插件" in items[1]
    assert "运行与安装" in items[2]
    assert '<p class="pg-hint">Python 插件继续使用' not in html
