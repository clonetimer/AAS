from pathlib import Path
import unittest


class TimeRangeLayoutUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "visualization" / "browser.html").read_text(encoding="utf-8")
        cls.light_css = (root / "web" / "visualization" / "styles-light.css").read_text(encoding="utf-8")
        cls.dark_css = (root / "web" / "visualization" / "styles.css").read_text(encoding="utf-8")

    def test_time_range_controls_follow_chart_and_precede_table(self):
        chart_pos = self.html.index('id="chart"')
        range_pos = self.html.index('id="timeRangeFilter"')
        table_pos = self.html.index('class="table-wrap"')
        self.assertLess(chart_pos, range_pos)
        self.assertLess(range_pos, table_pos)

    def test_time_range_controls_no_longer_live_in_filter_sidebar(self):
        aside_end = self.html.index('</aside>')
        range_pos = self.html.index('id="timeRangeFilter"')
        self.assertGreater(range_pos, aside_end)

    def test_both_themes_have_wide_chart_time_range_layout(self):
        for css in (self.light_css, self.dark_css):
            self.assertIn('.chart-time-range .time-range-control-row', css)
            self.assertIn('grid-template-columns: minmax(0, 1fr) auto;', css)
            self.assertIn('.chart-time-range .time-range-inputs', css)


if __name__ == "__main__":
    unittest.main()
