from pathlib import Path
import unittest


class SimulationFaultTablePreviewUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.js = (root / "web" / "visualization" / "simulation.js").read_text(encoding="utf-8")

    def test_fault_table_preview_no_longer_hard_limits_itself_to_first_400_rows(self):
        self.assertNotIn("limit=400", self.js)
        self.assertIn("FAULT_TABLE_PREVIEW_PAGE_SIZE = 5000", self.js)
        self.assertIn("async function loadAllFaultPreviewRows(tableName)", self.js)
        self.assertIn("while (offset < matchedCount)", self.js)
        self.assertIn("offset: String(offset)", self.js)
        self.assertIn("if (timeCol) params.set('time_col', timeCol);", self.js)

    def test_fault_table_preview_downsamples_only_for_drawing_and_preserves_full_axis(self):
        self.assertIn("FAULT_TABLE_PREVIEW_MAX_POINTS = 1200", self.js)
        self.assertIn("function downsampleFaultPreviewRows", self.js)
        self.assertIn("minPoint", self.js)
        self.assertIn("maxPoint", self.js)
        self.assertIn("index / Math.max(allRows.length - 1, 1)", self.js)
        self.assertIn("保峰值降采样", self.js)

    def test_fault_table_preview_reports_total_records_and_actual_drawn_points(self):
        self.assertIn("payload.total_count", self.js)
        self.assertIn("总计 ${totalCount} 条记录 · 实际绘制 ${plotted.length} 个点", self.js)
        self.assertIn("firstTime", self.js)
        self.assertIn("lastTime", self.js)


if __name__ == "__main__":
    unittest.main()
