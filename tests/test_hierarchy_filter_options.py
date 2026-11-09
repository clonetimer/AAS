from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.server.services import sqlite_hierarchy_options


class HierarchyFilterBackendTests(unittest.TestCase):
    def test_history_sample_exposes_variable_candidates(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / "datasets" / "history_sample.db").is_file():
            self.skipTest("旧历史数据库已从 V2 交付包中移除")
        payload = sqlite_hierarchy_options(root / "datasets" / "history_sample.db")
        self.assertIn("capacity", payload["variables"])
        self.assertIn("soc", payload["variables"])
        self.assertGreater(len(payload["variables"]), 1)

    def test_hierarchy_options_are_not_truncated_to_first_100_combinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "many_variables.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE telemetry (
                        model TEXT,
                        subsystem TEXT,
                        device TEXT,
                        variable TEXT,
                        value REAL
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO telemetry(model, subsystem, device, variable, value) VALUES (?, ?, ?, ?, ?)",
                    [("M1", "eps", "battery", f"var_{idx:03d}", float(idx)) for idx in range(150)],
                )
                conn.commit()

            payload = sqlite_hierarchy_options(db_path)
            self.assertEqual(150, len(payload["variables"]))
            self.assertIn("var_149", payload["variables"])

            filtered = sqlite_hierarchy_options(db_path, model="M1", subsystem="eps", device="battery")
            self.assertEqual(payload["variables"], filtered["variables"])


class HierarchyFilterUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.js = (root / "web" / "visualization" / "browser.js").read_text(encoding="utf-8")

    def test_parent_changes_update_checkbox_candidates(self):
        self.assertIn('function refreshMultipleBrowserFilters()', self.js)
        self.assertIn('const upstream = TABLE_LAYER_KEYS.slice(0, idx)', self.js)
        self.assertIn('state.multiFilters[key] = state.multiFilters[key].filter(', self.js)

    def test_table_selection_matches_a_real_hierarchy_row(self):
        self.assertIn('function tableMatches(table, through', self.js)
        self.assertIn("(table.hierarchy || []).some(row =>", self.js)


if __name__ == "__main__":
    unittest.main()
