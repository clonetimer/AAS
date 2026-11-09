from pathlib import Path
import unittest


class RunnerPluginImportNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner_html = (root / "web" / "visualization" / "runner.html").read_text(encoding="utf-8")
        cls.runner_js = (root / "web" / "visualization" / "runner.js").read_text(encoding="utf-8")
        cls.plugins_html = (root / "web" / "visualization" / "plugins.html").read_text(encoding="utf-8")
        cls.plugins_js = (root / "web" / "visualization" / "plugins.js").read_text(encoding="utf-8")

    def test_runner_no_longer_exposes_second_import_modal(self):
        self.assertIn('id="importAlgorithmBtn"', self.runner_html)
        self.assertNotIn('id="importAlgorithmModal"', self.runner_html)
        self.assertNotIn("/api/import-algorithm", self.runner_js)
        self.assertNotIn("/api/preview-algorithm", self.runner_js)

    def test_runner_redirects_detector_import_to_plugin_manager(self):
        self.assertIn("PLUGIN_IMPORT_INTENT_KEY", self.runner_js)
        self.assertIn("type: 'detectors'", self.runner_js)
        self.assertIn("language: 'python'", self.runner_js)
        self.assertIn("window.parent.location.hash = 'plugins'", self.runner_js)
        self.assertIn("plugins.html?install=detectors&language=python", self.runner_js)

    def test_plugin_manager_consumes_intent_and_opens_detector_install(self):
        self.assertIn('id="customPluginInstall"', self.plugins_html)
        self.assertIn("requestedInstallIntent()", self.plugins_js)
        self.assertIn("focusRequestedInstall(intent)", self.plugins_js)
        self.assertIn("installDetails.open = true", self.plugins_js)
        self.assertIn("typeSel.value = intent.type", self.plugins_js)
        self.assertIn("window.sessionStorage.removeItem(PLUGIN_IMPORT_INTENT_KEY)", self.plugins_js)


if __name__ == "__main__":
    unittest.main()
