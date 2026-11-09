from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.server.sqlite_guard import sqlite_connection as guarded_sqlite_connection

from scripts.server import services
from scripts import visualization_server


class SimulationDatabaseLifecycleTests(unittest.TestCase):

    def _make_paths(self, root: Path):
        datasets = root / "datasets"
        datasets.mkdir()
        history = datasets / "history_sample.db"
        simulation = datasets / "simulation.db"
        conn = sqlite3.connect(history)
        try:
            conn.execute("CREATE TABLE source_data (timestamp TEXT, value REAL, label INTEGER)")
            conn.execute("INSERT INTO source_data VALUES ('2026-01-01 00:00:00', 1.0, 0)")
            conn.commit()
        finally:
            conn.close()
        shutil.copy2(history, simulation)
        return history, simulation

    def test_delete_waits_for_in_process_simulation_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history, simulation = self._make_paths(root)
            attrs = {
                "HISTORY_SAMPLE_DB_PATH": history,
                "SIMULATION_DB_PATH": simulation,
                "ROOT": root,
            }
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            result_box = {}

            def reader():
                with guarded_sqlite_connection(
                    simulation, simulation_db_path=simulation
                ) as conn:
                    conn.execute("SELECT COUNT(*) FROM source_data").fetchone()
                    started.set()
                    release.wait(2.0)

            def deleter():
                result_box["result"] = visualization_server.delete_simulation_db()
                finished.set()

            patches = [patch.object(visualization_server, name, value) for name, value in attrs.items()]
            for ctx in patches:
                ctx.start()
            try:
                t_reader = threading.Thread(target=reader, daemon=True)
                t_delete = threading.Thread(target=deleter, daemon=True)
                t_reader.start()
                self.assertTrue(started.wait(1.0))
                t_delete.start()
                time.sleep(0.08)
                self.assertFalse(finished.is_set(), "delete should wait for an AAS-owned reader")
                release.set()
                self.assertTrue(finished.wait(2.0))
                self.assertEqual(result_box["result"]["status"], "success")
                self.assertFalse(simulation.exists())
            finally:
                release.set()
                for ctx in reversed(patches):
                    ctx.stop()

    def test_windows_sharing_violation_is_reported_as_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history, simulation = self._make_paths(root)
            exc = PermissionError(13, "file is in use", str(simulation))
            exc.winerror = 32
            attrs = {
                "HISTORY_SAMPLE_DB_PATH": history,
                "SIMULATION_DB_PATH": simulation,
                "ROOT": root,
            }
            patches = [patch.object(visualization_server, name, value) for name, value in attrs.items()]
            for ctx in patches:
                ctx.start()
            try:
                with patch.object(visualization_server, "simulation_unlink_with_retry", side_effect=exc):
                    result = visualization_server.delete_simulation_db()
                self.assertEqual(result["status"], "busy")
                self.assertFalse(result["deleted"])
                self.assertIn("正在被其他操作或进程占用", result["message"])
                self.assertIn("DB Browser", result["message"])
                self.assertTrue(simulation.exists())
            finally:
                for ctx in reversed(patches):
                    ctx.stop()
    def _exercise_module(self, module):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            datasets = root / "datasets"
            datasets.mkdir()
            history = datasets / "history_sample.db"
            simulation = datasets / "simulation.db"
            with sqlite3.connect(history) as conn:
                conn.execute("CREATE TABLE source_data (timestamp TEXT, value REAL, label INTEGER)")
                conn.execute("INSERT INTO source_data VALUES ('2026-01-01 00:00:00', 1.0, 0)")
                conn.execute("CREATE TABLE source_data_clean (timestamp TEXT, value REAL, label INTEGER)")
                conn.commit()

            attrs = {"HISTORY_SAMPLE_DB_PATH": history, "SIMULATION_DB_PATH": simulation}
            if hasattr(module, "PROJECT_ROOT"):
                attrs["PROJECT_ROOT"] = root
            if hasattr(module, "ROOT"):
                attrs["ROOT"] = root

            patches = [patch.object(module, name, value) for name, value in attrs.items()]
            for ctx in patches:
                ctx.start()
            try:
                first = module.init_simulation_db()
                self.assertEqual(first["status"], "success", first)
                self.assertTrue(simulation.exists())
                with sqlite3.connect(simulation) as conn:
                    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertIn("source_data", names)
                    self.assertNotIn("source_data_clean", names)
                    conn.execute("CREATE TABLE source_data_fault_spike_20260910_120000 (value REAL)")
                    conn.commit()

                exists = module.init_simulation_db()
                self.assertEqual(exists["status"], "exists")
                self.assertIn("重新初始化", exists["message"])

                rebuilt = module.init_simulation_db(force=True)
                self.assertEqual(rebuilt["status"], "success", rebuilt)
                self.assertTrue(rebuilt["reinitialized"])
                self.assertEqual(rebuilt["discarded_fault_tables"], 1)
                with sqlite3.connect(simulation) as conn:
                    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("source_data_fault_spike_20260910_120000", names)

                deleted = module.delete_simulation_db()
                self.assertEqual(deleted["status"], "success", deleted)
                self.assertTrue(deleted["deleted"])
                self.assertFalse(simulation.exists())
                self.assertTrue(history.exists())
            finally:
                for ctx in reversed(patches):
                    ctx.stop()

    def test_services_lifecycle(self):
        self._exercise_module(services)

    def test_visualization_server_lifecycle(self):
        self._exercise_module(visualization_server)

    def test_ui_exposes_state_aware_reset_and_delete_actions(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "visualization" / "simulation.html").read_text(encoding="utf-8")
        js = (root / "web" / "visualization" / "simulation.js").read_text(encoding="utf-8")
        self.assertIn('id="deleteSimBtn"', html)
        self.assertIn("重新初始化模拟数据库", js)
        self.assertIn("/api/simulation/delete", js)
        self.assertIn("force: exists", js)


if __name__ == "__main__":
    unittest.main()
