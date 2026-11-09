from __future__ import annotations

# pylint: disable=protected-access  # Tests deliberately invalidate the private caching hook to isolate configuration changes.

import tempfile
import time
import unittest
from pathlib import Path

from scripts.server import services


class DataImportAliasConfigTests(unittest.TestCase):
    def setUp(self):
        self.original_path = services.DATA_IMPORT_ALIASES_PATH
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "data_import_aliases.yaml"
        services.DATA_IMPORT_ALIASES_PATH = self.config_path
        services._reset_data_import_recognition_cache()

    def tearDown(self):
        services.DATA_IMPORT_ALIASES_PATH = self.original_path
        services._reset_data_import_recognition_cache()
        self.tmp.cleanup()

    def _write_config(self, *, label_column: str = "fault_flag") -> None:
        self.config_path.write_text(
            f'''version: 1
columns:
  timestamp: [sample_clock]
  label: [{label_column}]
  model: [satellite_type]
  subsystem: [sys_name]
  device: [unit_name]
  variable: [telemetry_name]
  value: [telemetry_value]
labels:
  normal: [NORMAL_STATE, N]
  fault: [FAULT_STATE, F]
''',
            encoding="utf-8",
        )

    def test_custom_yaml_aliases_and_label_values_are_used(self):
        self._write_config()
        csv_text = (
            "sample_clock,satellite_type,sys_name,unit_name,telemetry_name,telemetry_value,fault_flag\n"
            "0,GFX,eps,battery,soc,0.62,NORMAL_STATE\n"
            "10,GFX,eps,battery,soc,0.31,FAULT_STATE\n"
        )
        result = services.preview_import(csv_text, "csv", filename="field_data.csv")

        mappings = result["suggested_mappings"]
        self.assertEqual(mappings["timestamp"]["value"], "sample_clock")
        self.assertEqual(mappings["model"]["value"], "satellite_type")
        self.assertEqual(mappings["subsystem"]["value"], "sys_name")
        self.assertEqual(mappings["device"]["value"], "unit_name")
        self.assertEqual(mappings["variable"]["value"], "telemetry_name")
        self.assertEqual(mappings["value"]["value"], "telemetry_value")
        self.assertEqual(mappings["label"]["value"], "fault_flag")
        self.assertEqual(result["label_summary"], {"normal": 1, "fault": 1, "unknown": 0})
        self.assertEqual(result["suggested_role"], "test")
        self.assertEqual(result["recognition_config"]["source"], "yaml")
        self.assertEqual(Path(result["recognition_config"]["path"]), self.config_path)

    def test_config_is_reloaded_after_file_change_without_service_restart(self):
        self._write_config(label_column="fault_flag")
        first = services.preview_import(
            "sample_clock,fault_flag\n0,NORMAL_STATE\n10,FAULT_STATE\n",
            "csv",
        )
        self.assertEqual(first["suggested_mappings"]["label"]["value"], "fault_flag")

        # Change both content and size so filesystem cache keys change even on
        # filesystems with coarse timestamp resolution.
        time.sleep(0.01)
        self._write_config(label_column="status_flag_long")
        second = services.preview_import(
            "sample_clock,status_flag_long\n0,NORMAL_STATE\n10,FAULT_STATE\n",
            "csv",
        )
        self.assertEqual(second["suggested_mappings"]["label"]["value"], "status_flag_long")

    def test_invalid_yaml_is_reported_during_import_preview(self):
        self.config_path.write_text("columns: [not, an, object]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "columns 必须是对象"):
            services.preview_import("time,value\n0,1\n", "csv")


if __name__ == "__main__":
    unittest.main()
