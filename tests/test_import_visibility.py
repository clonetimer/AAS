from __future__ import annotations

import sqlite3
import unittest
import uuid

from scripts.server import services
from scripts import visualization_server


class ImportedTableVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"aas_import_visibility_{uuid.uuid4().hex}.db"
        self.db_path = services.PROJECT_ROOT / "datasets" / self.db_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # import_data only writes into an existing managed database.
        sqlite3.connect(self.db_path).close()

    def tearDown(self):
        try:
            self.db_path.unlink(missing_ok=True)
        except TypeError:  # pragma: no cover - Python < 3.8 compatibility
            if self.db_path.exists():
                self.db_path.unlink()

    def _import_non_gf_table(self):
        csv_text = "time,soc,fault\n0,0.62,0\n10,0.61,1\n"
        mappings = {
            "timestamp": {"type": "column", "value": "time"},
            "model": {"type": "manual", "value": "GFSIM1"},
            "subsystem": {"type": "manual", "value": "eps"},
            "device": {"type": "manual", "value": "battery"},
            "variable": {"type": "manual", "value": "soc"},
            "value": {"type": "column", "value": "soc"},
            "label": {"type": "column", "value": "fault"},
        }
        return services.import_data(
            csv_text,
            "csv",
            f"datasets/{self.db_name}",
            "TEST",
            "test_data_20260910",
            mappings,
        )

    def test_imported_custom_prefix_table_is_visible_in_browser_services(self):
        result = self._import_non_gf_table()
        self.assertEqual(result["table_name"], "TEST_test_data_20260910")
        self.assertEqual(result["imported_rows"], 2)

        # The table really exists in SQLite.
        with sqlite3.connect(self.db_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("TEST_test_data_20260910", names)

        # More importantly, the data-browser service must not hide it merely
        # because its prefix is TEST rather than GF.
        browser_tables = services.sqlite_tables(self.db_path)
        by_name = {item["name"]: item for item in browser_tables}
        self.assertIn("TEST_test_data_20260910", by_name)
        self.assertEqual(by_name["TEST_test_data_20260910"]["row_count"], 2)

        hierarchy = services.sqlite_hierarchy_options(self.db_path)
        self.assertIn("GFSIM1", hierarchy["models"])
        self.assertIn("eps", hierarchy["subsystems"])
        self.assertIn("battery", hierarchy["devices"])
        self.assertIn("soc", hierarchy["variables"])

        # The running HTTP server currently exposes compatibility functions
        # from visualization_server.py. Keep those in sync with the split
        # service layer so an API request cannot hide a table that unit-level
        # services can see.
        server_tables = visualization_server.sqlite_tables(self.db_path)
        self.assertIn(
            "TEST_test_data_20260910",
            {item["name"] for item in server_tables},
        )
        server_hierarchy = visualization_server.sqlite_hierarchy_options(self.db_path)
        self.assertIn("GFSIM1", server_hierarchy["models"])
        self.assertIn("eps", server_hierarchy["subsystems"])

    def test_internal_table_without_browser_schema_stays_hidden(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE dataset_meta (key TEXT, value TEXT)")
            conn.commit()
        names = {item["name"] for item in services.sqlite_tables(self.db_path)}
        self.assertNotIn("dataset_meta", names)


if __name__ == "__main__":
    unittest.main()
