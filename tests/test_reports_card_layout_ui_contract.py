from pathlib import Path
import unittest


class ReportsCardLayoutUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.reports_html = (root / "web" / "visualization" / "reports.html").read_text(encoding="utf-8")

    def test_card_grid_collapses_unused_columns(self):
        self.assertIn(
            "grid-template-columns: repeat(auto-fit, minmax(min(360px, 100%), 1fr));",
            self.reports_html,
        )
        self.assertNotIn(
            "grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));",
            self.reports_html,
        )
        self.assertIn("width: 100%;", self.reports_html)

    def test_single_detector_card_uses_wider_metric_layout(self):
        self.assertIn(
            ".detail-cards > .detail-card:only-child .metrics-grid",
            self.reports_html,
        )
        self.assertIn(
            "grid-template-columns: repeat(4, minmax(0, 1fr));",
            self.reports_html,
        )
        self.assertIn("@media (max-width: 760px)", self.reports_html)


if __name__ == "__main__":
    unittest.main()
