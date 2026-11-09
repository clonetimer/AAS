from pathlib import Path
import unittest


class SimulationFaultParamUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.js = (root / "web" / "visualization" / "simulation.js").read_text(encoding="utf-8")
        cls.registry = (root / "src" / "fault_plugins" / "registry.py").read_text(encoding="utf-8")

    def test_preview_completion_does_not_recreate_dynamic_parameter_inputs(self):
        self.assertIn(
            "updateFaultTypeFields({ renderParameters: false });",
            self.js,
        )
        self.assertIn(
            "function updateFaultTypeFields({ renderParameters = true } = {})",
            self.js,
        )
        self.assertIn(
            "if (renderParameters) renderFaultParameters();",
            self.js,
        )

    def test_periodic_frequency_is_a_real_editable_runtime_parameter(self):
        self.assertIn('"name": "frequency"', self.registry)
        self.assertIn('"default": 1.0', self.registry)
        self.assertIn('"min": 0.1', self.registry)
        self.assertIn('"step": 0.1', self.registry)
        self.assertIn("querySelectorAll('[data-fault-param]')", self.js)
        self.assertIn("config[input.dataset.faultParam] = value", self.js)


if __name__ == "__main__":
    unittest.main()
