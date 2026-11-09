from pathlib import Path
import sqlite3
import unittest

from scripts.server import services


class TrainingSourceSelectionGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.root = root
        cls.runner_html = (root / "web" / "visualization" / "runner.html").read_text(encoding="utf-8")
        cls.runner_js = (root / "web" / "visualization" / "runner.js").read_text(encoding="utf-8")
        cls.handlers_py = (root / "scripts" / "server" / "handlers.py").read_text(encoding="utf-8")
        cls.db_path = root / "datasets" / "history_sample.db"

    def test_training_page_only_lists_valid_training_tables(self):
        self.assertIn("function validTrainingTables(tables)", self.runner_js)
        self.assertIn("item.data_role === 'train' && item.role_valid !== false", self.runner_js)
        self.assertIn("updateTrainTestOptions(allTables)", self.runner_js)
        self.assertIn("state.tables = validTrainingTables(allTables)", self.runner_js)
        self.assertIn("训练页仅显示“训练集”", self.runner_html)

    def test_table_selection_updates_state_and_invalidates_old_result(self):
        self.assertIn("state.selectedTable = event.target.value || '';", self.runner_js)
        self.assertIn("invalidateTrainingResult();", self.runner_js)
        self.assertIn("const currentSourceTable = els.tableSelect?.value || state.selectedTable || '';", self.runner_js)
        self.assertIn("table: currentSourceTable", self.runner_js)

    def test_backend_simple_mode_rejects_non_training_role(self):
        self.assertIn("所选源数据表不是有效训练集", self.handlers_py)
        self.assertIn("source_info.get(\"data_role\") != \"train\"", self.handlers_py)

    def test_packaged_history_sample_has_many_explicit_training_tables(self):
        items = services.sqlite_tables(self.db_path)
        train = [item for item in items if item.get("data_role") == "train" and item.get("role_valid")]
        holdout = [item for item in items if item.get("data_role") == "test" and item.get("name", "").endswith("_test_holdout")]
        self.assertGreaterEqual(len(train), 40)
        self.assertEqual(len(holdout), 39)
        self.assertTrue(all(item.get("fault_count") == 0 for item in train))
        self.assertTrue(all(item.get("normal_count", 0) > 0 and item.get("fault_count", 0) > 0 for item in holdout))

    def test_example_train_and_holdout_are_disjoint(self):
        train_table = "GF1_subsys1_battery_soc_train"
        test_table = "GF1_subsys1_battery_soc_test_holdout"
        with sqlite3.connect(self.db_path) as conn:
            train_ids = {row[0] for row in conn.execute(f'SELECT id FROM "{train_table}"')}
            test_ids = {row[0] for row in conn.execute(f'SELECT id FROM "{test_table}"')}
            train_fault = conn.execute(f'SELECT COUNT(*) FROM "{train_table}" WHERE label <> 0').fetchone()[0]
            test_counts = dict(conn.execute(f'SELECT label, COUNT(*) FROM "{test_table}" GROUP BY label').fetchall())
        self.assertFalse(train_ids & test_ids)
        self.assertEqual(train_fault, 0)
        self.assertGreater(test_counts.get(0, 0), 0)
        self.assertGreater(test_counts.get(1, 0), 0)


if __name__ == "__main__":
    unittest.main()
