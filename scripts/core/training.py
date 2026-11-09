#!/usr/bin/env python
"""
训练编排器 - 核心模块
整合数据加载、算法执行、指标计算和模型保存的完整训练流程
"""
from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


from .algorithm_runner import AlgorithmResult
from .data_loader import DataBundle, extract_binary_labels
from .metrics_engine import MetricsResult
from .input_scaling import apply_scaler, fit_scaler, normalize_method
from .model_manager import save_model
from .training_plugins.registry import get_strategy
from detectors.registry import get_min_training_samples

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    """训练结果容器

    Attributes:
        experiment_id: 实验唯一标识
        algorithm: 算法名称
        dataset: 数据集名称
        table_name: 表名
        metrics: 评估指标结果
        model_info: 模型保存信息（路径等）
        detector: 检测器实例（自定义检测器）或 None（内置算法）
        threshold: 历史兼容字段，实际为测试样本异常分数列表
        train_stats: 训练集统计信息
        algorithm_result: 算法执行结果（完整对象）
    """
    experiment_id: str
    algorithm: str
    dataset: str
    table_name: str
    metrics: MetricsResult
    model_info: dict
    detector: Any = None
    threshold: List[float] = None
    train_stats: dict = None
    algorithm_result: AlgorithmResult = None
    result_rows: List[Dict[str, Any]] = field(default_factory=list)
    data_mode: str = ""
    data_contract: Dict[str, Any] = field(default_factory=dict)
    input_evidence: Dict[str, Any] = field(default_factory=dict)
    input_snapshot: Dict[str, Any] | None = None


class TrainingPipeline:
    """训练编排器

    整合完整的训练流程：
    1. 数据加载与划分（调用 DataLoader）
    2. 算法执行（调用 AlgorithmRunner 或自定义检测器）
    3. 指标计算（调用 MetricsEngine）
    4. 模型保存（调用 save_model）

    支持三种数据加载模式：
    - 双表模式：提供 train_table 和 test_table
    - 比例划分模式：提供 table 和 train_ratio
    - 简单模式：提供 table 且 train_ratio=1.0

    支持两种算法类型：
    - 内置统计算法：three_sigma, zscore, iqr
    - 自定义检测器：从 configs/detectors/{algorithm}.yaml 动态加载

    Attributes:
        db_path: SQLite 数据库路径
        limit: 最大加载行数，0 表示不限制
        train_ratio: 训练集比例
        progress_callback: 进度回调函数
    """

    def __init__(
        self,
        db_path: str | Path,
        limit: int = 0,
        train_ratio: float = 0.7,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        *,
        models_dir: str | Path | None = None,
    ) -> None:
        """初始化训练编排器

        Args:
            db_path: SQLite 数据库文件路径
            limit: 最大加载行数，0 表示不限制
            train_ratio: 训练集比例（0-1），仅在比例划分模式下使用
            progress_callback: 进度回调函数，签名为 (progress: int, message: str)
        """
        self.db_path = Path(db_path)
        self.models_dir = Path(models_dir).resolve() if models_dir is not None else None
        self.limit = limit
        self.train_ratio = train_ratio
        self.progress_callback = progress_callback
        self._dataset: str | None = None
        self._table_name: str | None = None
        self._preloaded_detector: Any = None
        self._persist_model = True
        self._algorithm_params: dict[str, Any] = {}
        self._data_contract: dict[str, Any] = {}
        from core.container import get_container
        self._container = get_container()

        logger.info("[TrainingPipeline] 初始化完成，数据库：%s", self.db_path)
        logger.info(
            "[TrainingPipeline] 参数：limit=%d, train_ratio=%.2f",
            self.limit, self.train_ratio,
        )

    def run(
        self,
        algorithm: str,
        table: Optional[str] = None,
        train_table: Optional[str] = None,
        test_table: Optional[str] = None,
        algorithm_params: Optional[Dict[str, Any]] = None,
        clean_table_prefix: Optional[str] = None,
        experiment_id: Optional[str] = None,
        strategy: str = "standard",
        preloaded_detector: Any = None,
        persist_model: bool = True,
        data_contract: Optional[Dict[str, Any]] = None,
    ) -> TrainingResult:
        """执行完整训练流程

        Args:
            algorithm: 算法名称（如 "three_sigma", "mlp_detector" 等）
            table: 单表表名（比例划分模式或简单模式）
            train_table: 训练集表名（双表模式）
            test_table: 测试集表名（双表模式）
            algorithm_params: 用户传入的算法参数，可选
            clean_table_prefix: 清洗后的表前缀（暂不使用）
            experiment_id: 实验ID，如果不提供则自动生成
            preloaded_detector: 已训练检测器；提供时仅评分，不再次调用 fit()
            persist_model: 是否保存本次运行产生的模型；已发布模型评估应设为 False
            data_contract: 模型输入数据契约。记录训练时使用原始/清洁数据以及预处理配置，
                供已发布模型评估时复用完全相同的数据尺度。

        Returns:
            TrainingResult 训练结果

        Raises:
            ValueError: 当参数组合无效时
            FileNotFoundError: 当数据库文件不存在时
        """
        del clean_table_prefix

        # 生成实验 ID
        if experiment_id is None:
            experiment_id = uuid.uuid4().hex[:12]

        algorithm_params = algorithm_params or {}
        # dataset 表示数据源（SQLite 文件），table_name 才表示数据表。
        # 旧实现把 table 名误写进模型 config.json 的 dataset 字段，导致历史模型
        # 补发布时注册表中的 dataset 信息错误。
        dataset = self.db_path.name
        self._dataset = dataset
        self._table_name = table or train_table
        self._preloaded_detector = preloaded_detector
        self._persist_model = bool(persist_model)
        self._algorithm_params = dict(algorithm_params)
        self._data_contract = deepcopy(data_contract or {})

        logger.info("=" * 60)
        logger.info("[TrainingPipeline] 开始训练实验：%s", experiment_id)
        logger.info("[TrainingPipeline] 算法：%s, 数据集：%s", algorithm, dataset)
        logger.info("=" * 60)

        try:
            # 1. 报告进度 10%
            self._report_progress(10, "开始加载数据...")

            # 2. 加载数据 - 进度 20%
            data_bundle = self._load_data(table, train_table, test_table)
            if data_bundle.metadata.get('v2_run_manifest'):
                self._data_contract['v2_run_manifest'] = deepcopy(data_bundle.metadata['v2_run_manifest'])
                # Pin the exact input layout the model is about to be fit on.
                # feature_columns alone is positional, so it cannot detect a
                # transposed or physically redefined column. The manifest holds
                # the order the matrix was actually built in, which may differ
                # from the caller's submitted order.
                from scripts.server.feature_schema import attest_feature_schema
                manifest = self._data_contract['v2_run_manifest']
                if manifest.get('coordinate_contract'):
                    self._data_contract['coordinate_contract']=deepcopy(manifest['coordinate_contract'])
                selection = self._data_contract.get('v2_run_selection') or {}
                feature_keys = manifest.get('feature_keys') or selection.get('feature_keys')
                if isinstance(feature_keys, list) and feature_keys:
                    if isinstance(selection.get('feature_keys'), list):
                        selection['feature_keys'] = list(feature_keys)
                    run_evidence = manifest.get('runs') or []
                    attest_feature_schema(
                        self._data_contract, keys=feature_keys,
                        semantics=run_evidence[0].get('semantic_features') if run_evidence else None,
                    )
            if data_bundle.metadata.get('v2_alignment'):
                self._data_contract['v2_source_manifest'] = deepcopy(data_bundle.metadata['v2_alignment'])
            if data_bundle.metadata.get('alignment'):
                self._data_contract['alignment'] = deepcopy(data_bundle.metadata['alignment'])
            if data_bundle.metadata.get('logical_series_manifest'):
                self._data_contract['logical_series_manifest'] = deepcopy(data_bundle.metadata['logical_series_manifest'])
            self._report_progress(20, f"数据加载完成，训练集={data_bundle.train_count}, 测试集={data_bundle.test_count}")

            # 评估的真值必须来自实际参与检测的测试样本的 label 列。
            # test_value_rows 已过滤无效 value；逐行提取才能保证标签和预测对齐。
            # 在拟合模型前就检查，避免无标签数据浪费训练时间或生成误导性指标。
            data_bundle.test_labels = extract_binary_labels(data_bundle.test_value_rows)
            if len(data_bundle.test_labels) != len(data_bundle.test_values):
                raise ValueError("评估数据校验失败：测试样本与真实 label 数量不一致")
            data_bundle.metadata["metric_label_source"] = "test.label"

            # 训练业务约束只作用于真正的拟合过程。已发布模型评估会传入
            # preloaded_detector，此时测试数据允许包含故障样本，不能套用训练约束。
            if self._preloaded_detector is None:
                validation = self._validate_training_data(
                    algorithm=algorithm,
                    data_bundle=data_bundle,
                )
                self._report_progress(23, validation["message"])

            from scripts.core.input_evidence import capture_stage, finish_evidence
            features = list(self._data_contract.get("feature_columns") or ["value"])
            raw_evidence = capture_stage(data_bundle, "raw", features)

            # 模型输入尺度与数据清理职责分离：无论选择原始/清洁数据，
            # detector 看到的数据都按训练集拟合的同一 scaler 归一化。
            # 已发布模型评估时只应用持久化参数，绝不在测试集上重新拟合。
            data_bundle = self._apply_model_input_scaling(data_bundle)
            scaling = (self._data_contract.get("model_input_scaling") or {})
            if scaling.get("enabled", False):
                self._report_progress(25, f"模型输入归一化完成：{scaling.get('method', 'min_max')}")

            model_evidence = capture_stage(data_bundle, "model", features)
            input_evidence, input_snapshot = finish_evidence(
                raw_evidence, model_evidence, data_bundle, features,
                self._data_contract.get("model_input_scaling", {}),
            )

            # 3~5. 由训练策略编排：算法执行 → 指标计算 → 模型保存
            selected = get_strategy(strategy) or get_strategy("standard")
            algorithm_result, metrics, model_info = selected.execute(
                self, algorithm, data_bundle, algorithm_params, experiment_id,
            )

            # 6. 构建并返回 TrainingResult - 进度 100%
            result = TrainingResult(
                experiment_id=experiment_id,
                algorithm=algorithm,
                dataset=dataset or "",
                table_name=self._table_name or "",
                metrics=metrics,
                model_info=model_info,
                detector=algorithm_result.detector,
                threshold=algorithm_result.thresholds,
                train_stats=algorithm_result.train_stats,
                algorithm_result=algorithm_result,
                result_rows=[
                    {**dict(row), "label": label,
                     "value": row.get((self._data_contract.get("feature_columns") or ["value"])[0]),
                     "model_input_value": float(model_value[0] if isinstance(model_value, list) else model_value)}
                    for row, model_value, label in zip(
                        data_bundle.test_value_rows, data_bundle.test_values, data_bundle.test_labels,
                    )
                ],
                data_mode=data_bundle.mode,
                data_contract=deepcopy(self._data_contract),
                input_evidence=input_evidence,
                input_snapshot=input_snapshot,
            )

            self._report_progress(100, "训练完成")
            logger.info("[TrainingPipeline] 实验 %s 训练成功完成", experiment_id)
            return result

        except Exception as e:
            logger.error("[TrainingPipeline] 实验 %s 训练失败：%s", experiment_id, e)
            raise

    def run_core(
        self,
        algorithm: str,
        data_bundle: DataBundle,
        algorithm_params: Dict[str, Any],
        experiment_id: str,
    ) -> tuple:
        """算法执行 → 指标计算 → 可选模型保存（供训练策略调用）。"""
        # V2 run-level boundaries must be preserved for causal temporal algorithms.
        # A run never borrows past samples from another satellite simulation.
        def segment_lengths(sample_rows):
            if not sample_rows or "run_source_id" not in sample_rows[0]:
                return None
            lengths=[];previous=None;seen=set()
            for row in sample_rows:
                current=(row.get("run_source_id"),row.get("label_block_id",0))
                if current!=previous:
                    if current in seen:
                        raise ValueError("a V2 run has non-contiguous sample blocks")
                    seen.add(current)
                    lengths.append(0);previous=current
                lengths[-1]+=1
            return lengths
        self._active_train_segments=segment_lengths(data_bundle.train_value_rows)
        self._active_test_segments=segment_lengths(data_bundle.test_value_rows)
        if getattr(self, "_preloaded_detector", None) is not None:
            self._report_progress(40, "开始使用已训练模型执行评估...")
            algorithm_result = self._container.algorithm_runner.run_preloaded(
                algorithm=algorithm,
                test_values=data_bundle.test_values,
                detector=self._preloaded_detector,
                test_segments=self._active_test_segments,
            )
        else:
            self._report_progress(40, "开始执行算法...")
            algorithm_result = self._run_algorithm(
                algorithm, data_bundle, algorithm_params, experiment_id,
            )
        self._report_progress(70, "算法执行完成")

        self._report_progress(80, "开始计算评估指标...")
        metrics = self._compute_metrics(data_bundle, algorithm_result)
        self._report_progress(85, "指标计算完成")

        model_info: dict = {}
        if getattr(self, "_persist_model", True):
            self._report_progress(90, "开始保存模型...")
            model_info = self._save_model_to_disk(
                experiment_id, algorithm, self._dataset or "", self._table_name or "",
                metrics, algorithm_result,
            )
            self._report_progress(95, "模型保存完成")
        else:
            self._report_progress(95, "评估模式：跳过模型持久化")
        return algorithm_result, metrics, model_info

    # ------------------------------------------------------------------
    # 内部方法：数据加载
    # ------------------------------------------------------------------

    def _load_data(
        self,
        table: Optional[str],
        train_table: Optional[str],
        test_table: Optional[str],
    ) -> DataBundle:
        """加载数据

        自动根据参数选择数据加载模式：
        - 双表模式：train_table 和 test_table 均提供
        - 比例划分模式：只提供 table 且 train_ratio < 1.0
        - 简单模式：只提供 table 且 train_ratio == 1.0

        Args:
            table: 单表表名
            train_table: 训练集表名
            test_table: 测试集表名

        Returns:
            DataBundle 数据加载结果

        Raises:
            ValueError: 当参数组合无效时
        """
        loader = self._container.make_data_loader(
            self.db_path,
            self.limit,
            self.train_ratio,
        )
        loader.feature_columns = list(self._data_contract.get('feature_columns') or ['value'])
        loader.v2_run_selection = self._data_contract.get('v2_run_selection')
        loader.v2_label_target = self._data_contract.get('v2_label_target')
        loader.v2_channel_ids = list(self._data_contract.get('v2_channel_ids') or [])
        loader.v2_role = self._data_contract.get('v2_role')
        loader.v2_expected_sources = self._data_contract.get('v2_expected_sources')
        loader.v2_alignment_contract = self._data_contract.get('v2_alignment') or {}
        loader.source_tables = list(self._data_contract.get('resolved_source_tables') or [])
        loader.logical_multi_sources = list(self._data_contract.get('logical_multi_sources') or [])
        loader.logical_series = self._data_contract.get('logical_series') or None
        loader.logical_tables = list(self._data_contract.get('logical_tables') or [])
        loader.logical_role = self._data_contract.get('logical_role')
        loader.alignment_tolerance_seconds = (self._data_contract.get('alignment_tolerance_seconds') or 0)
        loader.alignment_method = self._data_contract.get('alignment_method', 'nearest')
        loader.timeline_mode = self._data_contract.get('timeline_mode', 'anchor')
        loader.grid_interval_seconds = self._data_contract.get('grid_interval_seconds', 0)
        loader.label_alignment_method = self._data_contract.get('label_alignment_method', 'matched')
        window = self._data_contract.get('time_selection') or {}
        loader.time_column = window.get('column') or None
        loader.time_start = window.get('start') or None
        loader.time_end = window.get('end') or None
        return loader.load_and_split(
            train_table=train_table,
            test_table=test_table,
            table=table,
        )

    @staticmethod
    def _is_normal_label(value: Any) -> bool:
        """Return whether a label explicitly represents a normal sample.

        The project datasets use ``0`` for normal and ``1`` for fault. A few
        imported datasets may serialize the same meaning as strings, so the
        check accepts unambiguous normal aliases while rejecting missing or
        unknown labels.
        """
        if value is None:
            return False
        if isinstance(value, bool):
            return not value
        text = str(value).strip().lower()
        if text in {"normal", "正常", "false", "no"}:
            return True
        try:
            return float(text) == 0.0
        except (TypeError, ValueError):
            return False

    def _validate_training_data(
        self,
        algorithm: str,
        data_bundle: DataBundle,
    ) -> Dict[str, Any]:
        """Validate black-box training business constraints before fitting.

        Rules:
        1. Every training row must carry an explicit ``label``.
        2. Every training label must represent normal data (``label == 0``).
        3. Valid numeric training samples must meet the detector-specific
           minimum declared by ``detectors.registry``.

        These checks intentionally run before model-input scaling or detector
        fitting so a rejected request can never be reported as training success.
        """
        train_rows = list(data_bundle.train_rows or [])
        if not train_rows:
            raise ValueError("训练数据校验失败：训练集为空，请选择包含正常样本的训练数据。")

        missing_label_count = sum(1 for row in train_rows if "label" not in row)
        if missing_label_count:
            raise ValueError(
                "训练数据校验失败：训练数据缺少 label 字段，无法确认“仅正常数据”要求；"
                "请使用带正常/故障标记的数据表。"
            )

        unknown_label_count = sum(
            1
            for row in train_rows
            if row.get("label") is None
            or (
                not self._is_normal_label(row.get("label"))
                and str(row.get("label")).strip().lower()
                not in {"1", "1.0", "fault", "faulty", "异常", "故障", "true", "yes"}
            )
        )
        if unknown_label_count:
            raise ValueError(
                "训练数据校验失败：存在 "
                f"{unknown_label_count} 条无法识别的 label，无法确认训练集全部为正常数据。"
            )

        fault_count = sum(
            1 for row in train_rows if not self._is_normal_label(row.get("label"))
        )
        if fault_count:
            raise ValueError(
                "训练数据校验失败：训练数据必须仅包含正常样本（label=0）；"
                f"当前训练集共 {len(train_rows)} 条，检测到 {fault_count} 条故障/非正常样本。"
                "请更换为仅正常数据的训练表或调整训练数据范围。"
            )

        valid_count = len(data_bundle.train_values or [])
        minimum = get_min_training_samples(algorithm)
        if valid_count < minimum:
            raise ValueError(
                "训练数据量不足："
                f"算法 {algorithm} 至少需要 {minimum} 条有效训练样本，当前只有 {valid_count} 条；"
                "请更换数据或扩大训练数据范围。"
            )

        validation = {
            "normal_only": True,
            "train_row_count": len(train_rows),
            "valid_train_count": valid_count,
            "fault_count": 0,
            "min_training_samples": minimum,
            "algorithm": algorithm,
            "message": (
                f"训练数据校验通过：全部为正常样本，有效样本={valid_count}，"
                f"最低要求={minimum}"
            ),
        }
        data_bundle.metadata["training_validation"] = dict(validation)
        return validation

    def _apply_model_input_scaling(self, data_bundle: DataBundle) -> DataBundle:
        """Apply the model-input scaler to train/test values.

        New training runs always fit scaler parameters on the training split.
        Published-model evaluation receives persisted parameters and only
        applies them.  ``enabled=False`` exists solely for backward-compatible
        evaluation of legacy artifacts created before this contract existed.
        """
        contract = self._data_contract.setdefault(
            "model_input_scaling",
            {"enabled": True, "method": "min_max"},
        )
        if not isinstance(contract, dict):
            raise ValueError("data_contract.model_input_scaling 必须为对象")

        enabled = bool(contract.get("enabled", True))
        if not enabled:
            contract["enabled"] = False
            contract.setdefault("source", "legacy_passthrough")
            data_bundle.metadata["model_input_scaling"] = deepcopy(contract)
            return data_bundle

        method = normalize_method(contract.get("method", "min_max"))
        contract["enabled"] = True
        contract["method"] = method

        if getattr(self, "_preloaded_detector", None) is None:
            features = self._data_contract.get('feature_columns') or ['value']
            if len(features) > 1:
                parameters = {
                    'method': method,
                    'per_feature': {feature: fit_scaler([row[index] for row in data_bundle.train_values], method)
                                    for index, feature in enumerate(features)},
                }
            else:
                parameters = fit_scaler(data_bundle.train_values, method)
            contract["parameters"] = parameters
            contract["fit_scope"] = "training_values_only"
        else:
            parameters = contract.get("parameters")
            if not isinstance(parameters, dict) or not parameters:
                raise RuntimeError(
                    "已发布模型声明启用输入归一化，但模型配置缺少训练期 scaler 参数；"
                    "请重新训练并发布模型"
                )

        features = self._data_contract.get('feature_columns') or ['value']
        if len(features) > 1:
            per_feature = parameters.get('per_feature')
            if not isinstance(per_feature, dict) or list(per_feature) != features:
                raise ValueError('模型输入特征和归一化参数不匹配，请确认特征顺序与训练时一致')
            def transform(matrix):
                columns = [apply_scaler([row[index] for row in matrix], per_feature[name])
                           for index, name in enumerate(features)]
                return [list(row) for row in zip(*columns)]
            data_bundle.train_values = transform(data_bundle.train_values)
            data_bundle.test_values = transform(data_bundle.test_values)
        else:
            data_bundle.train_values = apply_scaler(data_bundle.train_values, parameters)
            data_bundle.test_values = apply_scaler(data_bundle.test_values, parameters)
        data_bundle.metadata["model_input_scaling"] = deepcopy(contract)
        return data_bundle

    # ------------------------------------------------------------------
    # 内部方法：算法执行
    # ------------------------------------------------------------------

    def _run_algorithm(
        self,
        algorithm: str,
        data_bundle: DataBundle,
        algorithm_params: Dict[str, Any],
        experiment_id: str,
    ) -> AlgorithmResult:
        """执行算法

        所有算法（内置统计检测器与自定义 YAML 检测器）统一委托给
        ``AlgorithmRunner``，后者通过 detectors.loader 加载并评分，从而与
        CLI (runner/runner_entry) 共用同一加载契约。

        Args:
            algorithm: 算法名称
            data_bundle: 数据加载结果
            algorithm_params: 算法参数
            experiment_id: 实验ID

        Returns:
            AlgorithmResult 算法执行结果
        """
        del experiment_id
        logger.info("[TrainingPipeline] 开始执行算法：%s", algorithm)
        runner = self._container.algorithm_runner
        return runner.run(
            algorithm=algorithm,
            train_values=data_bundle.train_values,
            test_values=data_bundle.test_values,
            algorithm_params=algorithm_params,
            train_segments=getattr(self,"_active_train_segments",None),
            test_segments=getattr(self,"_active_test_segments",None),
        )

    # ------------------------------------------------------------------
    # 内部方法：指标计算
    # ------------------------------------------------------------------

    def _compute_metrics(
        self,
        data_bundle: DataBundle,
        algorithm_result: AlgorithmResult,
    ) -> MetricsResult:
        """计算评估指标

        使用测试集真实 ``label`` 与检测器最终预测标签计算分类指标。

        Args:
            data_bundle: 数据加载结果
            algorithm_result: 算法执行结果

        Returns:
            MetricsResult 评估指标结果
        """
        engine = self._container.web_metric_engine
        return engine.compute_metrics(
            y_true=data_bundle.test_labels,
            train_values=data_bundle.train_values,
            test_values=data_bundle.test_values,
            algorithm_scores=algorithm_result.thresholds,
            score_semantics=algorithm_result.score_semantics,
            decision_threshold=algorithm_result.decision_threshold,
            pred_label=algorithm_result.pred_labels or None,
        )

    # ------------------------------------------------------------------
    # 内部方法：模型保存
    # ------------------------------------------------------------------

    def _save_model_to_disk(
        self,
        experiment_id: str,
        algorithm: str,
        dataset: str,
        table_name: str,
        metrics: MetricsResult,
        algorithm_result: AlgorithmResult,
    ) -> dict:
        """保存模型到磁盘

        Args:
            experiment_id: 实验ID
            algorithm: 算法名称
            dataset: 数据集名称
            table_name: 表名
            metrics: 评估指标结果
            algorithm_result: 算法执行结果

        Returns:
            模型保存信息字典
        """
        detector_config = getattr(algorithm_result.detector, "config", None)
        if not isinstance(detector_config, dict):
            detector_config = dict(getattr(self, "_algorithm_params", {}) or {})
        config = {
            "algorithm_params": dict(detector_config),
            "train_stats": algorithm_result.train_stats or {},
            "algorithm_type": algorithm_result.algorithm_type,
            "data_contract": dict(getattr(self, "_data_contract", {}) or {}),
        }

        return save_model(
            experiment_id=experiment_id,
            algorithm=algorithm,
            dataset=dataset,
            table_name=table_name,
            metrics=metrics.to_dict(),
            detector=algorithm_result.detector,
            config=config,
            models_dir=self.models_dir,
        )

    # ------------------------------------------------------------------
    # 内部方法：进度回调
    # ------------------------------------------------------------------

    def _report_progress(self, progress: int, message: str) -> None:
        """报告进度

        Args:
            progress: 进度百分比（0-100）
            message: 进度消息
        """
        if self.progress_callback is not None:
            try:
                self.progress_callback(progress, message)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("[TrainingPipeline] 进度回调执行失败：%s", e)

        logger.debug("[TrainingPipeline] 进度 %d%%: %s", progress, message)
