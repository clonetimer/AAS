import unittest
from pathlib import Path


class RunnerEvaluationPreviewUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner_html = (root / 'web' / 'visualization' / 'runner.html').read_text(encoding='utf-8')
        cls.runner_js = (root / 'web' / 'visualization' / 'runner.js').read_text(encoding='utf-8')

    def test_evaluation_visual_panel_is_visible_before_evaluation(self):
        self.assertIn('id="evalResultPanel" class="panel training-visual-panel"', self.runner_html)
        self.assertIn('id="evalVisualizationTitle">评估数据预览</h2>', self.runner_html)
        self.assertIn('选择测试数据表后显示测试数据曲线', self.runner_html)
        self.assertIn('id="evalResultTableWrap" class="table-wrap" hidden', self.runner_html)

    def test_evaluation_preview_has_independent_database_state(self):
        self.assertIn('evalSelectedDb: null', self.runner_js)
        self.assertIn('evalSelectedTable: null', self.runner_js)
        self.assertIn('evalRows: []', self.runner_js)
        start = self.runner_js.index('async function loadEvalTables()')
        end = self.runner_js.index('async function loadEvalTableRows()', start)
        load_tables = self.runner_js[start:end]
        self.assertIn('state.evalSelectedDb =', load_tables)
        self.assertNotIn('state.selectedDb =', load_tables)

    def test_test_table_selection_immediately_loads_source_preview(self):
        self.assertIn('async function loadEvalTableRows()', self.runner_js)
        self.assertIn('state.evalPreviewRequestId', self.runner_js)
        self.assertIn('table=${encodeURIComponent(requestedTable)}&limit=1200', self.runner_js)
        self.assertIn("renderSourcePreviewChart(state.evalRows || [], els.evalChart, state.evalDataView)", self.runner_js)
        self.assertIn("els.evalTableSelect.addEventListener('change'", self.runner_js)

    def test_evaluation_uses_evaluation_database_instead_of_training_database(self):
        self.assertIn("dataset: state.evalSelectedDb?.value || ''", self.runner_js)
        self.assertIn("table: state.evalSelectedTable || els.evalTableSelect.value || ''", self.runner_js)

    def test_evaluation_result_reuses_preview_chart_area(self):
        self.assertIn('function showEvaluationResultVisualization()', self.runner_js)
        self.assertIn("'评估结果'", self.runner_js)
        self.assertIn('showEvaluationResultVisualization();', self.runner_js)
        self.assertIn('renderChart(rows, threshold, algorithm, els.evalChart, decisionThreshold', self.runner_js)
        self.assertIn('renderEvaluationSourcePreview();', self.runner_js)


if __name__ == '__main__':
    unittest.main()
