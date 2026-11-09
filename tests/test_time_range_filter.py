from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.server.services import sqlite_rows, sqlite_time_range


class TimeRangeBackendTests(unittest.TestCase):
    def test_text_timestamp_range_queries_full_table_not_only_first_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "time_text.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE telemetry(timestamp TEXT, model TEXT, value REAL, label INTEGER)"
                )
                rows = [
                    (f"2026-09-10 10:{idx:02d}:00", "M1", float(idx), idx % 2)
                    for idx in range(10)
                ]
                conn.executemany(
                    "INSERT INTO telemetry(timestamp, model, value, label) VALUES (?, ?, ?, ?)", rows
                )
                conn.commit()

            meta = sqlite_time_range(db_path, "telemetry", "timestamp")
            self.assertEqual("2026-09-10 10:00:00", meta["min"])
            self.assertEqual("2026-09-10 10:09:00", meta["max"])
            self.assertEqual(10, meta["total_count"])

            payload = sqlite_rows(
                db_path,
                "telemetry",
                limit=50,
                time_col="timestamp",
                start_time="2026-09-10 10:03:00",
                end_time="2026-09-10 10:05:00",
            )
            self.assertEqual(10, payload["total_count"])
            self.assertEqual(3, payload["matched_count"])
            self.assertFalse(payload["truncated"])
            self.assertEqual([3.0, 4.0, 5.0], [row["value"] for row in payload["rows"]])

    def test_numeric_time_range_and_truncation_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "time_numeric.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE telemetry(time_s REAL, value REAL)")
                conn.executemany(
                    "INSERT INTO telemetry(time_s, value) VALUES (?, ?)",
                    [(float(idx * 10), float(idx)) for idx in range(20)],
                )
                conn.commit()

            meta = sqlite_time_range(db_path, "telemetry", "time_s")
            self.assertEqual("number", meta["value_type"])
            self.assertEqual(0.0, meta["min"])
            self.assertEqual(190.0, meta["max"])

            payload = sqlite_rows(
                db_path,
                "telemetry",
                limit=3,
                time_col="time_s",
                start_time="50",
                end_time="100",
            )
            self.assertEqual(6, payload["matched_count"])
            self.assertTrue(payload["truncated"])
            self.assertEqual([50.0, 60.0, 70.0], [row["time_s"] for row in payload["rows"]])

    def test_invalid_time_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "invalid.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE telemetry(timestamp TEXT, value REAL)")
                conn.execute("INSERT INTO telemetry VALUES ('2026-09-10 10:00:00', 1.0)")
                conn.commit()

            with self.assertRaises(ValueError):
                sqlite_time_range(db_path, "telemetry", "missing")
            with self.assertRaises(ValueError):
                sqlite_rows(db_path, "telemetry", time_col="missing")


class TimeRangeUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "visualization" / "browser.html").read_text(encoding="utf-8")
        cls.js = (root / "web" / "visualization" / "browser.js").read_text(encoding="utf-8")

    def test_ui_has_explicit_start_end_and_apply_clear_controls(self):
        for marker in [
            'id="timeRangeStart"',
            'id="timeRangeEnd"',
            'id="applyTimeRangeBtn"',
            'id="clearTimeRangeBtn"',
            'id="timeRangeStatus"',
        ]:
            self.assertIn(marker, self.html)

    def test_ui_queries_server_side_time_range(self):
        self.assertIn("/api/sqlite-time-range", self.js)
        self.assertIn("params.set('time_col'", self.js)
        self.assertIn("params.set('start_time'", self.js)
        self.assertIn("params.set('end_time'", self.js)
        self.assertIn("TIME_RANGE_ROW_LIMIT = 5000", self.js)
        self.assertIn("async function applyTimeRange()", self.js)
        self.assertIn("async function clearTimeRange()", self.js)

    def test_cursor_zoom_and_plot_clipping_remain_enabled(self):
        self.assertIn("function setChartZoomAt", self.js)
        self.assertIn("chartCursorRatio", self.js)
        self.assertIn("browserChartPlotClip", self.js)
        self.assertIn("滚轮：以光标为中心缩放", self.js)


if __name__ == "__main__":
    unittest.main()
