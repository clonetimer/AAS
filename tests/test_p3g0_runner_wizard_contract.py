from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runner_has_guided_workflow_steps_without_replacing_existing_views():
    html = (ROOT / 'web/visualization/runner.html').read_text(encoding='utf-8')
    for label in ('选择数据', '检查数据', '训练模型', '选择测试数据', '评估或仅推理', '查看结果'):
        assert label in html
    for target in ('trainCsvImport', 'previewTrain', 'trainAlgorithm', 'evalCsvImport', 'evalModel', 'evalResultTab'):
        assert target in html


def test_runner_wizard_only_navigates_existing_actions():
    js = (ROOT / 'web/visualization/runner-workflow-guide.js').read_text(encoding='utf-8')
    assert "'tabTrain'" in js
    assert "'tabEval'" in js
    assert "getElementById('runTrain')" not in js
    assert "getElementById('runEval')" not in js


def test_unlabeled_inference_is_explained_as_not_supervised_evaluation():
    html = (ROOT / 'web/visualization/runner.html').read_text(encoding='utf-8')
    assert '无标签' in html
    assert '不生成监督指标' in html
