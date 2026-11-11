from pathlib import Path


def test_home_quick_access_exposes_complete_user_workflow():
    html = (Path(__file__).resolve().parents[1] / "web" / "visualization" / "home.html").read_text(encoding="utf-8")
    expected = {
        "/browser.html": "历史遥测数据库",
        "/simulation.html": "故障模拟数据库",
        "/preprocess.html": "数据预处理",
        "/runner.html": "算法训练与评估",
        "/reports.html": "实验结果与导出",
        "/plugins.html": "检测算法管理",
        "/monitor.html": "软件性能监控",
    }
    for href, label in expected.items():
        assert 'href="{}"'.format(href) in html
        assert label in html
    assert 'class="module-number">07</span>' in html
