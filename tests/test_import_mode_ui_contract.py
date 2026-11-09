from pathlib import Path
import unittest


class ImportModeUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "web" / "visualization" / "browser.html").read_text(encoding="utf-8")
        cls.js = (root / "web" / "visualization" / "browser.js").read_text(encoding="utf-8")

    def test_single_and_batch_modes_are_explicit_in_ui(self):
        self.assertIn('name="importMode" value="single"', self.html)
        self.assertIn('name="importMode" value="batch"', self.html)
        self.assertIn('id="singleImportOptions"', self.html)
        self.assertIn('id="batchImportOptions"', self.html)
        self.assertIn('id="batchValidation"', self.html)

    def test_modes_use_separate_import_paths_and_batch_validation(self):
        self.assertIn("if (importMode === 'single')", self.js)
        self.assertIn("/api/import-data'", self.js)
        self.assertIn("/api/import-data-multi'", self.js)
        self.assertIn('批量导入规范检查未通过', self.js)
        self.assertIn("importEls.fieldMapping.hidden = true", self.js)


if __name__ == "__main__":
    unittest.main()
