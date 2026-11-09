import unittest
from pathlib import Path


class RunnerTrainingPreviewUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runner_html = (root / 'web' / 'visualization' / 'runner.html').read_text(encoding='utf-8')
        cls.runner_js = (root / 'web' / 'visualization' / 'runner.js').read_text(encoding='utf-8')

    def test_training_visual_panel_is_visible_before_training(self):
        self.assertIn('id="trainResultPanel" class="panel training-visual-panel"', self.runner_html)
        self.assertIn('id="trainVisualizationTitle">训练数据预览</h2>', self.runner_html)
        self.assertIn('选择训练数据表后显示当前数据视图曲线', self.runner_html)
        self.assertIn('id="trainChartFullscreenBtn"', self.runner_html)

    def test_selected_table_renders_source_preview(self):
        self.assertIn('function renderSourcePreviewChart(rows, chartEl = els.chart, dataView = state.dataView)', self.runner_js)
        self.assertIn('function renderTrainingSourcePreview()', self.runner_js)
        self.assertIn('renderTrainingSourcePreview();\n}', self.runner_js)
        self.assertIn('&limit=1200', self.runner_js)

    def test_training_result_reuses_same_chart_area(self):
        self.assertIn('function showTrainingResultVisualization()', self.runner_js)
        self.assertIn("'训练结果'", self.runner_js)
        self.assertIn('showTrainingResultVisualization();', self.runner_js)
        self.assertIn('renderChart(rows, threshold, resultAlgorithm, els.chart, decisionThreshold, metrics, decisionThresholds, predLabels)', self.runner_js)
        self.assertIn('id="trainResultTableWrap" class="table-wrap" hidden', self.runner_html)

    def test_data_view_switch_loads_clean_rows_instead_of_relabeling_raw_rows(self):
        self.assertIn("status?.status !== 'valid'", self.runner_js)
        self.assertIn("status.clean_table || status.clean_table_name", self.runner_js)
        self.assertIn("encodeURIComponent(cleanTable)", self.runner_js)
        self.assertNotIn("state.preprocessingStatus.valid", self.runner_js)
        self.assertNotIn("加载清洁数据失败，回退到原始数据", self.runner_js)

    def test_clean_preview_uses_physical_scale_and_reports_unavailable_state(self):
        self.assertIn('清理后的物理量曲线（未应用模型输入归一化）', self.runner_js)
        self.assertIn('清洁数据尚未生成，请先点击“执行预处理”', self.runner_js)
        self.assertIn('切换后右侧预览会同步显示对应的物理量曲线', self.runner_html)
        self.assertIn("normalize_method: 'none'", self.runner_js)

    def test_preview_requests_ignore_stale_view_switch_responses(self):
        self.assertIn('previewRequestId: 0', self.runner_js)
        self.assertIn('const requestId = ++state.previewRequestId;', self.runner_js)
        self.assertIn('state.dataView === requestedView', self.runner_js)
        self.assertIn('renderSourcePreviewChart(cached.rows, chartEl, cached.dataView)', self.runner_js)


if __name__ == '__main__':
    unittest.main()
