"""Read-only, evidence-based comparison of archived AAS experiments.

Identical raw or model input digests are *observations*, not a claim that
algorithms, internal randomness, hardware or scientific protocols are equal.
Legacy reports without input evidence are explicitly marked unverifiable.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

import gzip
import json
import math
import re
from pathlib import Path
from typing import Any


_EXPERIMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_SPLITS = ("train", "test")


def _split_equal(first: dict, second: dict, stage: str, split: str) -> bool | None:
    """Return None rather than claiming equality for incomplete evidence."""
    left = (first.get("stages") or {}).get(stage, {}).get(split)
    right = (second.get("stages") or {}).get(stage, {}).get(split)
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    if not all(isinstance(entry.get("sha256"), str) and len(entry["sha256"]) == 64
               and isinstance(entry.get("rows"), int)
               for entry in (left, right)):
        return None
    return left["sha256"] == right["sha256"] and left["rows"] == right["rows"]


def _selection_equal(first: dict, second: dict, split: str) -> bool | None:
    """Compare timestamps, mapped source timestamps and labels, not values."""
    left = (first.get("sample_selection") or {}).get(split)
    right = (second.get("sample_selection") or {}).get(split)
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    if not all(isinstance(e.get("sha256"), str) and len(e["sha256"]) == 64
               and isinstance(e.get("rows"), int) for e in (left, right)):
        return None
    return left == right


def compare_manifests(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Classify fairness of inputs without imposing a detector preprocessing policy."""
    if not all(isinstance(e, dict) and isinstance(e.get('stages'), dict)
               and isinstance(e.get('feature_columns'), list) for e in (first, second)):
        return {'classification': 'insufficient_evidence', 'comparable_inputs': None,
                'reason': '至少一个实验没有可验证的模型输入摘要'}
    features_same = first['feature_columns'] == second['feature_columns']
    raw = {s: _split_equal(first, second, 'raw', s) for s in _SPLITS}
    model = {s: _split_equal(first, second, 'model', s) for s in _SPLITS}
    selection = {s: _selection_equal(first, second, s) for s in _SPLITS}
    result = {
        'feature_order_identical': features_same,
        'raw_inputs_identical': raw,
        'model_inputs_identical': model,
        'sample_selection_identical': selection,
        'scaling_parameters_identical': (
            first.get('scaling_sha256') == second.get('scaling_sha256')
            if first.get('scaling_sha256') and second.get('scaling_sha256') else None),
        'alignment_parameters_identical': (
            first.get('alignment_sha256') == second.get('alignment_sha256')
            if first.get('alignment_sha256') and second.get('alignment_sha256') else None),
        'comparable_inputs': None,
    }
    if any(v is None for v in (*raw.values(), *model.values())):
        result.update(classification='insufficient_evidence',
                      reason='缺少完整训练或测试输入摘要，不能确认可比性')
    elif features_same and all(model.values()):
        result.update(classification='identical_model_inputs', comparable_inputs=True,
                      reason='两个实验在模型入口实际接收的训练和测试数据、标签、顺序均一致；不保证算法内部随机性一致')
    elif features_same and all(raw.values()):
        result.update(classification='same_raw_inputs_different_preprocessing',
                      comparable_inputs=False,
                      reason='输入样本及原始数值一致，但模型入口数据不同；适合比较整套预处理方案，不能作为同输入算法比较')
    elif features_same and all(v is True for v in selection.values()):
        result.update(classification='same_samples_different_values', comparable_inputs=False,
                      reason='训练及测试样本时间与标签一致，但数值或特征处理不同')
    else:
        result.update(classification='different_or_unconfirmed_inputs', comparable_inputs=False,
                      reason='无法证实训练与测试的输入完全一致；应检查选择的表、时间范围、划分及特征映射')
    return result



# These have comparable frame-level/supervised semantics in AAS V2.
# Do not compare timing, runtime, arbitrary scores, or user-defined metrics.
COMPARABLE_SUPERVISED_METRICS = frozenset((
    'accuracy', 'precision', 'recall', 'f1', 'f1_score', 'roc_auc',
    'false_positive_rate', 'false_alarm_rate', 'event_recall',
    'median_first_alarm_delay_seconds',
))


def _metric_comparison(left: dict, right: dict, left_result: dict, right_result: dict,
                       classification: str) -> dict:
    """Return optional verified supervised values, never a model ranking."""
    unavailable = {'status': 'not_comparable', 'rows': [], 'direction': 'right_minus_left'}
    if classification != 'identical_model_inputs' or any(
            summary.get('experiment', {}).get('type') != 'evaluation' and
            summary.get('type') != 'evaluation'
            for summary in (left, right)):
        return {**unavailable, 'reason': '只有同一模型输入的两次监督评估才展示可对照指标'}
    if any(not isinstance(summary.get('evaluation'), dict) or
           summary['evaluation'].get('ground_truth_available') is not True or
           summary['evaluation'].get('evaluation_mode', 'supervised') != 'supervised'
           for summary in (left, right)):
        return {**unavailable, 'reason': '实验缺少真实故障标签或监督评估声明，不能比较准确率等指标'}
    lhs = left_result.get('metrics') if isinstance(left_result, dict) else None
    rhs = right_result.get('metrics') if isinstance(right_result, dict) else None
    if not isinstance(lhs, dict) or not isinstance(rhs, dict):
        return {'status': 'missing_metrics', 'rows': [], 'direction': 'right_minus_left',
                'reason': '实验结果没有可用的监督指标'}
    rows = []
    for key in sorted(lhs.keys() & rhs.keys() & COMPARABLE_SUPERVISED_METRICS):
        a, b = lhs[key], rhs[key]
        if (isinstance(a, bool) or isinstance(b, bool) or
                not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or
                not math.isfinite(a) or not math.isfinite(b)):
            continue
        rows.append({'key': key, 'left': float(a), 'right': float(b), 'delta': float(b - a)})
    if not rows:
        return {'status': 'missing_metrics', 'rows': [], 'direction': 'right_minus_left',
                'reason': '两个实验没有名称和口径均一致的已保存监督指标'}
    return {'status': 'comparable', 'rows': rows, 'direction': 'right_minus_left',
            'reason': '仅供相同输入条件下的指标对照；差值为第二个实验减第一个实验，不代表自动排名'}

def experiment_summary(root: Path, experiment_id: str) -> tuple[Path, dict]:
    """Resolve only server-owned experiment summaries, with no arbitrary path input."""
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError('实验编号包含非法字符')
    root = Path(root).resolve()
    summary = (root / 'outputs' / f'experiment_{experiment_id}' / 'reports' / 'summary.json')
    resolved = summary.resolve()
    if not is_relative_to(resolved, root / 'outputs') or not resolved.is_file():
        raise ValueError('实验报告不存在或不是受管目录')
    try:
        content = json.loads(resolved.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('实验报告不可读取') from exc
    if not isinstance(content, dict) or str(content.get('experiment_id')) != experiment_id:
        raise ValueError('实验编号和归档内容不匹配')
    return resolved, content


def compare_archived_experiments(root: Path, first_id: str, second_id: str) -> dict:
    """Verify both archives before comparing server-owned input evidence."""
    from scripts.verify_experiment import verify

    if first_id == second_id:
        raise ValueError('请选择两个不同的实验')
    left_path, left = experiment_summary(root, first_id)
    right_path, right = experiment_summary(root, second_id)
    left_status = verify(left_path)['status']
    right_status = verify(right_path)['status']
    verified = left_status == right_status == 'verified_archive_digest'
    if not verified:
        return {'classification': 'unverified_archives', 'comparable_inputs': None,
                'left': {'id': first_id, 'archive_status': left_status},
                'right': {'id': second_id, 'archive_status': right_status},
                'reason': '至少一个归档未通过本地 SHA-256 一致性验证，拒绝将其用于可信输入比较'}
    # Digest-only snapshots cannot be reconstructed, so compare only when the
    # input manifest is also bound to the independently hashed execution result.
    verified_results = []
    for path, summary in ((left_path, left), (right_path, right)):
        with gzip.open(path.parent / 'execution_result.json.gz', 'rb') as handle:
            result = json.load(handle)
        provenance = summary.get('provenance') or {}
        embedded = result.get('input_evidence') if isinstance(result, dict) else None
        if embedded is None or embedded != provenance.get('effective_model_inputs'):
            return {'classification': 'insufficient_evidence', 'comparable_inputs': None,
                    'left': {'id': first_id, 'archive_status': left_status},
                    'right': {'id': second_id, 'archive_status': right_status},
                    'reason': '输入清单未同时嵌入受摘要保护的完整服务端结果，无法核实数据输入可比性'}
        verified_results.append(result)
    comparison = compare_manifests(
        (left.get('provenance') or {}).get('effective_model_inputs') or {},
        (right.get('provenance') or {}).get('effective_model_inputs') or {},
    )
    # Input equality is not identical to experimental repeatability. Disclose
    # algorithm/runtime/seed differences without disqualifying comparisons of
    # legitimately different detectors on the same controlled inputs.
    def _same_present(first: dict, second: dict, key: str) -> bool | None:
        a, b = first.get(key), second.get(key)
        return a == b if a is not None and b is not None else None

    with (left_path.parent / 'execution_config.json').open('r', encoding='utf-8') as handle:
        left_config = json.load(handle)
    with (right_path.parent / 'execution_config.json').open('r', encoding='utf-8') as handle:
        right_config = json.load(handle)
    lp, rp = (left.get('provenance') or {}), (right.get('provenance') or {})
    comparison['repeatability_controls'] = {
        'same_algorithm': _same_present(left_config, right_config, 'algorithm'),
        'same_algorithm_parameters': _same_present(left_config, right_config, 'algorithmParams'),
        'same_runtime': _same_present(lp, rp, 'runtime'),
        'same_implementation_digest': _same_present(lp, rp, 'algorithm_implementation'),
        'same_recorded_randomness': _same_present(lp, rp, 'randomness'),
    }
    comparison['left'] = {'id': first_id, 'archive_status': left_status,
                          'type': left.get('type'), 'randomness': lp.get('randomness')}
    comparison['right'] = {'id': second_id, 'archive_status': right_status,
                           'type': right.get('type'), 'randomness': rp.get('randomness')}
    if left.get('type') != right.get('type'):
        comparison['classification'] = 'different_task_types'
        comparison['reason'] = '训练和评估任务不属于同类实验，不应直接比较结果指标'
        comparison['comparable_inputs'] = False
    comparison['metric_comparison'] = _metric_comparison(
        left, right, verified_results[0], verified_results[1], comparison['classification'])
    comparison['scope_note'] = ('只能证明存档所记载的数据摘要一致性；不代表算法实现、随机序列、'
                                '运行环境、阈值或报警判决相同。摘要不是第三方签名。')
    return comparison
