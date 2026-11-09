from __future__ import annotations

import sqlite3
import unittest
import uuid
from pathlib import Path

from core.plugin_runtime import PluginRuntime
from scripts.server import services
from scripts import visualization_server
from src.fault_injection import create_injector
from src.fault_plugins.registry import (
    FAULT_TYPE_REGISTRY,
    available_fault_types,
    normalize_fault_config,
)


class FaultTypePluginTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.runtime = PluginRuntime(self.root)
        suffix = uuid.uuid4().hex[:10]
        self.name = f"test_loss_fault_{suffix}"
        self.module_name = self.name
        self.yaml_name = f"{self.name}.yaml"
        self.code_name = f"{self.module_name}.py"
        self.yaml_content = f"""name: {self.name}
label: 测试容量损失
description: 在目标区间按比例降低数值
formula: y′ = y · (1 - loss_ratio)
class_path: src.fault_plugins.{self.module_name}.TestLossFault
uses_severity: false
supports_time_range: true
dependencies: []
config_schema:
  - name: loss_ratio
    label: 损失比例
    type: float
    default: 0.25
    min: 0
    max: 1
    step: 0.05
"""
        self.code_content = """from src.fault_plugins.base import BaseFaultType

class TestLossFault(BaseFaultType):
    def inject(self, data, config):
        ratio = float(config.get('loss_ratio', 0.25))
        def apply(row):
            value = row.get('value')
            if value is None:
                return value
            return value * (1.0 - ratio)
        return self.apply_to_range(data, config, apply)
"""
        self.db_name = f"fault_plugin_{suffix}.db"
        self.db_path = self.root / "datasets" / self.db_name
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE sample (timestamp TEXT, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)"
            )
            conn.executemany(
                "INSERT INTO sample VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("2026-01-01 00:00:00", "GF", "eps", "battery", "soc", 100.0, 0),
                    ("2026-01-01 00:00:10", "GF", "eps", "battery", "soc", 80.0, 0),
                ],
            )
            conn.commit()

    def tearDown(self):
        try:
            self.runtime.uninstall("fault_types", self.name, force=True)
        except Exception:  # pylint: disable=broad-exception-caught
            # Invalid/partially installed plugins may not be removable during teardown.
            pass
        FAULT_TYPE_REGISTRY.pop(self.name, None)
        self.db_path.unlink(missing_ok=True)

    def _install(self):
        preview = self.runtime.preview(
            "fault_types", self.yaml_name, self.yaml_content,
            self.code_name, self.code_content,
        )
        self.assertTrue(preview["valid"], preview)
        result = self.runtime.install(
            "fault_types", self.yaml_name, self.yaml_content,
            self.code_name, self.code_content,
        )
        self.assertTrue(result["registered"], result)
        return result

    def test_builtin_fault_types_remain_available(self):
        names = {item["name"] for item in available_fault_types()}
        self.assertTrue({"spike", "drift", "break", "periodic", "noise"}.issubset(names))

    def test_custom_fault_type_install_and_runtime_injection(self):
        self._install()
        item = next(item for item in available_fault_types() if item["name"] == self.name)
        self.assertEqual(item["label"], "测试容量损失")
        self.assertFalse(item["builtin"])
        self.assertFalse(item["uses_severity"])
        self.assertEqual(item["config_schema"][0]["name"], "loss_ratio")

        config = normalize_fault_config(self.name, {"mode": "full", "loss_ratio": 0.4})
        injector = create_injector(self.name)
        rows = injector.inject([{"timestamp": "2026-01-01 00:00:00", "value": 100.0}], config)
        self.assertAlmostEqual(rows[0]["value"], 60.0)

    def test_custom_fault_type_is_used_by_simulation_preview(self):
        self._install()
        result = services.preview_fault_injection(
            f"datasets/{self.db_name}", "sample", self.name,
            {"mode": "full", "loss_ratio": 0.25},
        )
        self.assertNotIn("error", result)
        self.assertEqual([row["value"] for row in result["original"]], [100.0, 80.0])
        self.assertEqual([row["value"] for row in result["faulty"]], [75.0, 60.0])

        # Keep the compatibility functions exposed by visualization_server in sync.
        server_items = {item["name"]: item for item in visualization_server.get_simulation_fault_types()}
        self.assertIn(self.name, server_items)
        self.assertFalse(server_items[self.name]["builtin"])


    def test_original_curve_preview_requires_no_fault_type_plugin(self):
        result = services.preview_fault_injection(
            f"datasets/{self.db_name}", "sample", "__original__", {}
        )
        self.assertNotIn("error", result)
        self.assertEqual(result["view_mode"], "original")
        self.assertEqual(result["faulty"], [])
        self.assertEqual([row["value"] for row in result["original"]], [100.0, 80.0])

    def test_visualization_server_original_curve_wrapper_uses_canonical_service(self):
        result = visualization_server.preview_fault_injection(
            f"datasets/{self.db_name}", "sample", "__original__", {}
        )
        self.assertNotIn("error", result)
        self.assertEqual(result.get("view_mode"), "original")
        self.assertEqual(result.get("fault_type"), "__original__")
        self.assertEqual(result.get("faulty"), [])
        self.assertEqual([row["value"] for row in result["original"]], [100.0, 80.0])

    def test_visualization_server_original_mode_cannot_be_injected(self):
        result = visualization_server.execute_fault_injection("sample", "__original__", {})
        self.assertEqual(result.get("status"), "error")
        self.assertIn("原始曲线模式不执行故障注入", result.get("message", ""))

    def test_ui_exposes_original_curve_mode(self):
        html = (self.root / "web" / "visualization" / "simulation.html").read_text(encoding="utf-8")
        js = (self.root / "web" / "visualization" / "simulation.js").read_text(encoding="utf-8")
        self.assertIn("曲线模式 / 故障类型", html)
        self.assertIn("ORIGINAL_VIEW_MODE", js)
        self.assertIn("原始曲线", js)
        self.assertIn("无需注入", js)

    def test_custom_schema_validation_rejects_out_of_range_value(self):
        self._install()
        with self.assertRaisesRegex(ValueError, "不能大于 1"):
            normalize_fault_config(self.name, {"mode": "full", "loss_ratio": 1.5})


if __name__ == "__main__":
    unittest.main()
