from __future__ import annotations

import sqlite3
import unittest
import uuid

from scripts.server import services


class DataRoleAndBatchImportTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"aas_batch_import_{uuid.uuid4().hex}.db"
        self.db_path = services.PROJECT_ROOT / "datasets" / self.db_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(self.db_path).close()

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def _wide_csv(self, *, with_fault: bool) -> str:
        rows = [
            "time_s,target_name,eps.battery.soc,eps.battery.storage_wh,label.fault_active",
            "0,battery,0.62,99.2,False",
            "10,battery,0.61,98.1,False",
        ]
        if with_fault:
            rows.append("20,battery,0.40,70.0,True")
        else:
            rows.append("20,battery,0.60,97.0,False")
        return "\n".join(rows) + "\n"

    def test_preview_auto_recognizes_columns_role_and_telemetry(self):
        result = services.preview_import(
            self._wide_csv(with_fault=True),
            "csv",
            filename="测试集_电池开路故障.csv",
        )
        self.assertEqual(result["suggested_mappings"]["timestamp"]["value"], "time_s")
        self.assertEqual(result["suggested_mappings"]["label"]["value"], "label.fault_active")
        self.assertEqual(result["suggested_role"], "test")
        self.assertEqual(result["dataset_tag"], "test_open_circuit")
        telemetry = {item["column"]: item for item in result["telemetry_columns"]}
        self.assertIn("eps.battery.soc", telemetry)
        self.assertEqual(telemetry["eps.battery.soc"]["subsystem"], "eps")
        self.assertEqual(telemetry["eps.battery.soc"]["device"], "battery")
        self.assertEqual(telemetry["eps.battery.soc"]["variable"], "soc")

    def test_multi_import_expands_one_file_to_multiple_parameter_tables(self):
        csv_text = self._wide_csv(with_fault=True)
        common = {
            "timestamp": {"type": "column", "value": "time_s"},
            "model": {"type": "manual", "value": "GFSIM1"},
            "label": {"type": "column", "value": "label.fault_active"},
        }
        jobs = []
        for variable, source in [
            ("soc", "eps.battery.soc"),
            ("storage_wh", "eps.battery.storage_wh"),
        ]:
            mappings = dict(common)
            mappings.update(
                {
                    "subsystem": {"type": "manual", "value": "eps"},
                    "device": {"type": "manual", "value": "battery"},
                    "variable": {"type": "manual", "value": variable},
                    "value": {"type": "column", "value": source},
                }
            )
            jobs.append(
                {
                    "table_prefix": "",
                    "table_name": f"GFSIM1_eps_battery_{variable}_test_open_circuit",
                    "data_role": "test",
                    "field_mappings": mappings,
                }
            )

        result = services.import_data_multi(
            csv_text,
            "csv",
            f"datasets/{self.db_name}",
            jobs,
            source_file="测试集_电池开路故障.csv",
        )
        self.assertEqual(result["tables_created"], 2)
        self.assertEqual(result["imported_rows_total"], 6)

        tables = {item["name"]: item for item in services.sqlite_tables(self.db_path)}
        for variable in ("soc", "storage_wh"):
            name = f"GFSIM1_eps_battery_{variable}_test_open_circuit"
            self.assertIn(name, tables)
            self.assertEqual(tables[name]["data_role"], "test")
            self.assertTrue(tables[name]["role_valid"])
            self.assertEqual(tables[name]["normal_count"], 2)
            self.assertEqual(tables[name]["fault_count"], 1)

        with sqlite3.connect(self.db_path) as conn:
            labels = conn.execute(
                'SELECT label FROM "GFSIM1_eps_battery_soc_test_open_circuit" ORDER BY rowid'
            ).fetchall()
        # Boolean strings from the source CSV are normalized to the binary AAS contract.
        self.assertEqual([row[0] for row in labels], [0, 0, 1])

    def test_declared_training_role_rejects_fault_rows(self):
        mappings = {
            "timestamp": {"type": "column", "value": "time_s"},
            "model": {"type": "manual", "value": "GFSIM1"},
            "subsystem": {"type": "manual", "value": "eps"},
            "device": {"type": "manual", "value": "battery"},
            "variable": {"type": "manual", "value": "soc"},
            "value": {"type": "column", "value": "eps.battery.soc"},
            "label": {"type": "column", "value": "label.fault_active"},
        }
        with self.assertRaisesRegex(ValueError, "训练集必须仅包含正常数据"):
            services.import_data(
                self._wide_csv(with_fault=True),
                "csv",
                f"datasets/{self.db_name}",
                "GFSIM1",
                "eps_battery_soc_train",
                mappings,
                data_role="train",
            )

    def test_auto_role_marks_all_normal_table_as_training(self):
        mappings = {
            "timestamp": {"type": "column", "value": "time_s"},
            "model": {"type": "manual", "value": "GFSIM1"},
            "subsystem": {"type": "manual", "value": "eps"},
            "device": {"type": "manual", "value": "battery"},
            "variable": {"type": "manual", "value": "soc"},
            "value": {"type": "column", "value": "eps.battery.soc"},
            "label": {"type": "column", "value": "label.fault_active"},
        }
        services.import_data(
            self._wide_csv(with_fault=False),
            "csv",
            f"datasets/{self.db_name}",
            "GFSIM1",
            "eps_battery_soc_train",
            mappings,
            data_role="auto",
        )
        item = next(item for item in services.sqlite_tables(self.db_path) if item["name"].endswith("_train"))
        self.assertEqual(item["data_role"], "train")
        self.assertEqual(item["role_label"], "训练集")
        self.assertTrue(item["role_valid"])
        self.assertEqual(item["fault_count"], 0)


if __name__ == "__main__":
    unittest.main()
