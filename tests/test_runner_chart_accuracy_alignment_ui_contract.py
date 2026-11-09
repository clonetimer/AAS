from pathlib import Path
import unittest


class RunnerChartAccuracyAlignmentUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner_js = (root / "web" / "visualization" / "runner.js").read_text(encoding="utf-8")
        cls.light_css = (root / "web" / "visualization" / "styles-light.css").read_text(encoding="utf-8")
        cls.dark_css = (root / "web" / "visualization" / "styles.css").read_text(encoding="utf-8")

    def test_result_chart_compares_score_with_actual_decision_limit(self):
        self.assertIn("window.AASResultChart.render(chartEl", self.runner_js)
        self.assertIn("decision_threshold: decisionThreshold", self.runner_js)
        self.assertIn("decision_thresholds: decisionThresholds", self.runner_js)
        self.assertIn("pred_labels: predLabels", self.runner_js)
        self.assertNotIn("const sortedThreshold = [...threshold]", self.runner_js)


    def test_axis_labels_use_edge_anchors_instead_of_hardcoded_offset(self):
        self.assertIn('text-anchor="start" fill="var(--muted)"', self.runner_js)
        self.assertIn('text-anchor="end" fill="var(--muted)"', self.runner_js)
        self.assertNotIn('width - padding.right - 220', self.runner_js)

    def test_svg_geometry_matches_actual_chart_size_to_avoid_text_stretching(self):
        self.assertIn('function getChartGeometry(chartEl)', self.runner_js)
        self.assertIn('chartEl?.clientWidth', self.runner_js)
        self.assertIn('chartEl?.clientHeight', self.runner_js)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', self.runner_js)
        self.assertIn('scheduleChartGeometryRefresh', self.runner_js)

    def test_chart_svg_text_uses_ui_font_in_both_themes(self):
        for css in (self.light_css, self.dark_css):
            self.assertIn('.runner-page .runner-result-chart .chart-svg text', css)
            self.assertIn('font-family: var(--font-sans);', css)


if __name__ == "__main__":
    unittest.main()
