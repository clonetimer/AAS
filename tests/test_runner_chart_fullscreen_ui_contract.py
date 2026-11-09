from pathlib import Path
import unittest


class RunnerChartFullscreenUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner_html = (root / "web" / "visualization" / "runner.html").read_text(encoding="utf-8")
        cls.runner_js = (root / "web" / "visualization" / "runner.js").read_text(encoding="utf-8")
        cls.index_html = (root / "web" / "visualization" / "index.html").read_text(encoding="utf-8")
        cls.light_css = (root / "web" / "visualization" / "styles-light.css").read_text(encoding="utf-8")
        cls.dark_css = (root / "web" / "visualization" / "styles.css").read_text(encoding="utf-8")

    def test_training_and_evaluation_charts_have_fullscreen_controls(self):
        self.assertIn('id="trainChartViewer"', self.runner_html)
        self.assertIn('id="trainChartFullscreenBtn"', self.runner_html)
        self.assertIn('id="evalChartViewer"', self.runner_html)
        self.assertIn('id="evalChartFullscreenBtn"', self.runner_html)

    def test_rendered_svg_fills_chart_and_has_fullscreen_logic(self):
        self.assertIn('class="chart-svg"', self.runner_js)
        self.assertIn('toggleChartFullscreen', self.runner_js)
        self.assertIn("document.addEventListener('fullscreenchange'", self.runner_js)

    def test_workbench_iframe_allows_fullscreen(self):
        self.assertIn('allow="fullscreen"', self.index_html)
        self.assertIn('allowfullscreen', self.index_html)

    def test_both_themes_fill_svg_and_support_fullscreen(self):
        for css in (self.light_css, self.dark_css):
            self.assertIn('.runner-page .runner-result-chart > svg', css)
            self.assertIn('height: 100%;', css)
            self.assertIn('.runner-page .result-chart-viewer:fullscreen', css)
            self.assertIn('.runner-page .result-chart-viewer.chart-fullscreen-fallback', css)


if __name__ == "__main__":
    unittest.main()
