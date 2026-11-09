"""Training/evaluation orchestration extracted from the HTTP bootstrap.

Dependencies enter through an explicit application context; no module scanning,
server socket creation, or hidden storage initialization occurs on import.
"""
from __future__ import annotations
import math
from typing import Any

def run_evaluation(app, dataset: str, table: str, algorithm: str,
                   train_table: str = None,
                   test_table: str = None,
                   train_ratio: float = 0.7,
                   use_clean_data: bool = False,
                   auto_preprocess: bool = True,
                   normalization_method: str = "min_max",
                   input_scaling_contract: dict[str, Any] | None = None,
                   preloaded_detector=None,
                   persist_model: bool = True,
                   limit: int = 400,
                   feature_columns: list[str] | None = None,
                   source_tables: list[str] | None = None,
                   alignment_tolerance_seconds: float = 0,
                   alignment_method: str = 'nearest',
                   timeline_mode: str = 'anchor',
                   grid_interval_seconds: float = 0,
                   label_alignment_method: str = 'matched',
                   time_column: str | None = None,
                   time_start: str | None = None,
                   time_end: str | None = None,
                   task_id: str = None,
                   logical_multi_sources: list[dict] | None = None,
                   logical_series: dict[str, str] | None = None,
                   logical_tables: list[str] | None = None,
                   logical_role: str | None = None,
                   v2_channel_ids: list[int] | None = None,
                   v2_role: str | None = None,
                   v2_expected_sources: list[dict] | None = None,
                   v2_run_selection: dict | None = None,
                   v2_label_target: dict | None = None,
                   **kwargs) -> dict[str, Any]:
    resolve_managed_database_path = app.resolve_managed_database_path
    _base_table_name = app._base_table_name
    _resolve_clean_table = app._resolve_clean_table
    active_name = app.active_name
    TrainingPipeline = app.TrainingPipeline
    """Run training/evaluation while preserving the model input-data contract.

    A model trained on a ``*_clean`` table must be evaluated on the same data
    scale.  ``data_contract`` is persisted with newly trained models and is
    restored by ``/api/evaluate-model``.  When clean data is requested, this
    function never silently falls back to the raw source table.
    """

    def report_progress(progress, message):
        if task_id:
            app.update_task_progress(task_id, progress, message)
        app.console_log(f"[进度:{progress}%] {message}")

    db_path = resolve_managed_database_path(dataset)
    pipeline_config = kwargs.get("pipeline_config", {}) or {}
    if not isinstance(pipeline_config, dict):
        raise ValueError("pipeline_config 必须为对象")

    source_table = _base_table_name(table)
    source_train_table = _base_table_name(train_table)
    source_test_table = _base_table_name(test_table)

    actual_table = source_table
    actual_train_table = source_train_table
    actual_test_table = source_test_table

    if use_clean_data:
        if source_train_table and source_test_table:
            actual_train_table = _resolve_clean_table(
                db_path,
                source_train_table,
                auto_preprocess=auto_preprocess,
                pipeline_config=pipeline_config,
            )
            actual_test_table = _resolve_clean_table(
                db_path,
                source_test_table,
                auto_preprocess=auto_preprocess,
                pipeline_config=pipeline_config,
            )
        elif source_table:
            actual_table = _resolve_clean_table(
                db_path,
                source_table,
                auto_preprocess=auto_preprocess,
                pipeline_config=pipeline_config,
            )

    if isinstance(alignment_tolerance_seconds, bool) or not isinstance(alignment_tolerance_seconds, (int, float)) or not math.isfinite(alignment_tolerance_seconds) or not 0 <= alignment_tolerance_seconds <= 3600:
        raise ValueError('时间匹配容差必须是 0～3600 秒')
    if alignment_method not in (('nearest', 'previous', 'exact') if v2_channel_ids is not None or v2_run_selection is not None else ('nearest', 'previous')):
        raise ValueError('时间同步方式必须为 nearest 或 previous')
    if timeline_mode not in ('anchor', 'fixed'):
        raise ValueError('时间轴模式必须为 anchor 或 fixed')
    if label_alignment_method not in ('matched', 'previous', 'interval'):
        raise ValueError('标签映射方式必须为 matched 或 previous')
    if (isinstance(grid_interval_seconds, bool) or
            not isinstance(grid_interval_seconds, (int, float)) or
            not math.isfinite(grid_interval_seconds)):
        raise ValueError('采样周期必须为有限数值')
    if timeline_mode == 'fixed' and (not 0.001 <= grid_interval_seconds <= 86400 or alignment_tolerance_seconds <= 0):
        raise ValueError('固定时间轴需要 0.001～86400 秒的采样间隔和正数时间容差')
    if timeline_mode == 'anchor' and grid_interval_seconds != 0:
        raise ValueError('主表时间轴不能指定固定采样间隔')
    multi_alignment = bool(source_tables or logical_multi_sources or (v2_channel_ids is not None and len(v2_channel_ids) > 1) or (v2_run_selection is not None and len(v2_run_selection['feature_keys']) > 1))
    if not multi_alignment and (alignment_tolerance_seconds and v2_channel_ids is None and v2_run_selection is None or timeline_mode != 'anchor' or label_alignment_method not in ('matched', 'interval')):
        raise ValueError('时间同步参数只适用于多表训练')
    if not multi_alignment and alignment_tolerance_seconds and v2_channel_ids is None and v2_run_selection is None:
        raise ValueError('时间容差只适用于多表训练')
    if source_tables is not None:
        if not isinstance(source_tables, list) or len(source_tables) < 2 or len(source_tables) > 16 or len(set(source_tables)) != len(source_tables):
            raise ValueError('source_tables 必须包含 2～16 个不重复的数据表')
        if any(not isinstance(item, str) or not item.strip() for item in source_tables):
            raise ValueError('多表名称必须是非空字符串')
        if train_table or test_table:
            raise ValueError('多表训练暂不支持独立训练/测试表组')
        source_table = _base_table_name(source_tables[0])
        actual_table = source_table
        resolved_source_tables = [
            _resolve_clean_table(db_path, _base_table_name(name),
                                 auto_preprocess=auto_preprocess,
                                 pipeline_config=pipeline_config)
            if use_clean_data else _base_table_name(name)
            for name in source_tables
        ]
    else:
        resolved_source_tables = []

    if logical_multi_sources is not None:
        from scripts.core.logical_multi_fusion import resolve_logical_sources
        if logical_series is not None or source_tables or train_table or test_table or use_clean_data:
            raise ValueError('多逻辑参量仅允许原始数据、按时间比例划分，不可混用单参量或物理表对齐')
        if logical_role not in ('train', 'test'):
            raise ValueError('跨表联合必须指定 train/test 数据角色')
        resolved = resolve_logical_sources(db_path,
            [entry.get('identity') for entry in logical_multi_sources], role=logical_role)
        if resolved != logical_multi_sources:
            raise ValueError('来源表或数据用途已变化，请重新选择逻辑参量')
        if time_column not in (None, 'timestamp'):
            raise ValueError('跨表联合只允许 timestamp 时间列')
        if label_alignment_method == 'interval':
            raise ValueError('多逻辑参量暂不支持故障事件区间标签映射')
        time_column = 'timestamp'

    if logical_series is not None:
        from scripts.server.logical_selection import normalize_identity
        logical_series = normalize_identity(logical_series)
        if source_tables or train_table or test_table or use_clean_data:
            raise ValueError('逻辑参量模式仅支持原始数据单参量时间拼接，不能混用清洁表或多表特征融合')
        if not isinstance(logical_tables, list) or not logical_tables or len(set(logical_tables)) != len(logical_tables):
            raise ValueError('逻辑参量缺少经过用途校验的来源分片清单')
        if logical_role not in ('train', 'test'):
            raise ValueError('逻辑参量必须指定 train/test 数据用途')
        if time_column not in (None, 'timestamp'):
            raise ValueError('逻辑参量时间列必须为 timestamp')
        if label_alignment_method != 'matched':
            raise ValueError('逻辑参量当前仅支持逐样本真实标签')
        time_column = 'timestamp'

    if v2_run_selection is not None:
        from scripts.server.v2_run_dataset import _parse_selection
        _parse_selection(v2_run_selection, role=v2_role)
        from scripts.server.v2_label_targets import normalize_target
        v2_label_target=normalize_target(v2_label_target)
        if v2_role=='train' and v2_label_target['scope']!='run':
            raise ValueError('训练只接受正常运行标签；目标级真值用于独立评估')
        if db_path.name != active_name() or source_tables or logical_series or logical_multi_sources or train_table or test_table or use_clean_data:
            raise ValueError('运行级 V2 子集只能使用单一 V2 数据库，不能使用旧物理表配置')
        if time_column not in (None, 'timestamp'):
            raise ValueError('V2 数据必须使用统一 timestamp 接口')
        time_column='timestamp'

    if v2_channel_ids is not None:
        if db_path.name != active_name():
            raise ValueError('V2 训练和评估只允许使用安装的统一遥测数据库')
        if source_tables or logical_series or logical_multi_sources or train_table or test_table or use_clean_data:
            raise ValueError('V2 训练评估不支持旧物理表或清洁表模式')
        _args(v2_channel_ids,v2_role,alignment_method if alignment_method in ('exact','nearest','previous') else 'exact',alignment_tolerance_seconds,time_start,time_end)
        if time_column not in (None,'timestamp'):
            raise ValueError('V2 必须使用统一 timestamp 时间列')
        # The source manifest is validated by DataLoader during actual execution.
        time_column = 'timestamp'

    algorithm_params = kwargs.get("algorithm_params", {}) or {}
    training_strategy = kwargs.get("strategy") or kwargs.get("training_strategy") or "standard"

    from scripts.core.input_scaling import normalize_method

    if input_scaling_contract is not None:
        if not isinstance(input_scaling_contract, dict):
            raise ValueError("input_scaling_contract 必须为对象")
        scaling_contract = dict(input_scaling_contract)
        # enabled=False is reserved for evaluating legacy models that predate
        # the persisted scaler contract. Fresh training never takes this path.
        if scaling_contract.get("enabled", True):
            scaling_contract["enabled"] = True
            scaling_contract["method"] = normalize_method(
                scaling_contract.get("method", normalization_method)
            )
    else:
        scaling_contract = {
            "enabled": True,
            "method": normalize_method(normalization_method),
        }

    feature_count = (len(v2_run_selection['feature_keys']) if v2_run_selection is not None else (len(v2_channel_ids) if v2_channel_ids is not None else (len(logical_multi_sources) if logical_multi_sources else len(resolved_source_tables))))
    features = (['value'] + [f'source_{i}' for i in range(2, feature_count + 1)]) \
        if feature_count else (feature_columns if feature_columns is not None else ['value'])
    if not isinstance(features, list) or not features or any(not isinstance(x,str) or not x.strip() for x in features):
        raise ValueError('feature_columns 必须是非空特征名列表')
    if len(set(features)) != len(features):
        raise ValueError('不能重复选择特征列')
    if len(features) > 32:
        raise ValueError('最多选择 32 个特征列')
    data_contract = {
        "version": 7 if v2_run_selection is not None else (6 if v2_channel_ids is not None else (5 if logical_multi_sources else (4 if logical_series else 3))),
        "v2_run_selection": v2_run_selection,
        "coordinate_contract": ({"mode":"frame","frame_origin":1,
            "alignment":"frame_index","frame_sequence_policy":"independent"}
            if v2_run_selection is not None and v2_run_selection.get('coordinate_mode')=='frame'
            else {"mode":"time","alignment":"timestamp"} if v2_run_selection is not None else None),
        "v2_label_target": v2_label_target,
        "v2_channel_ids": list(v2_channel_ids or []),
        "v2_role": v2_role,
        "v2_expected_sources": v2_expected_sources,
        "data_source_provenance": (v2_expected_sources or {}).get('data_source_provenance'),
        "v2_alignment": {"method":alignment_method,"tolerance_seconds":alignment_tolerance_seconds} if v2_channel_ids is not None else None,
        "logical_multi_sources": logical_multi_sources or [],
        "logical_series": logical_series,
        "logical_tables": list(logical_tables or []),
        "logical_role": logical_role,
        "feature_columns": list(features),
        "source_tables": list(source_tables or []),
        "resolved_source_tables": list(resolved_source_tables),
        "source_mapping": dict(zip(features, source_tables or [])) if source_tables else {},
        "alignment_policy": (("previous_with_tolerance" if alignment_method == 'previous' else "nearest_unique_with_tolerance") if alignment_tolerance_seconds else "exact_timestamp_inner_join") if multi_alignment else None,
        "alignment_tolerance_seconds": float(alignment_tolerance_seconds) if multi_alignment else 0,
        "alignment_method": alignment_method if multi_alignment else 'nearest',
        "timeline_mode": timeline_mode if multi_alignment else 'anchor',
        "grid_interval_seconds": float(grid_interval_seconds) if multi_alignment else 0,
        "label_alignment_method": label_alignment_method if multi_alignment or label_alignment_method == 'interval' else 'matched',
        "label_policy": (
            ('simulation_run_state_ground_truth' if (v2_label_target or {}).get('scope')=='run'
             else 'explicit_target_truth_unknown_excluded') if v2_run_selection is not None
            else ('v2_channel_consensus_only' if v2_channel_ids is not None else
                  ('any_source_fault' if multi_alignment else
                   ('explicit_intervals_unknown_excluded' if label_alignment_method == 'interval' else 'source_label')))
        ),
        "time_selection": {"column": time_column, "start": v2_run_selection.get("start") if v2_run_selection is not None else time_start, "end": v2_run_selection.get("end") if v2_run_selection is not None else time_end},
        "data_view": "clean" if use_clean_data else "raw",
        "use_clean_data": bool(use_clean_data),
        "auto_preprocess": bool(auto_preprocess),
        "pipeline_config": dict(pipeline_config) if use_clean_data else {},
        "model_input_scaling": scaling_contract,
        "source_table": source_table,
        "source_train_table": source_train_table,
        "source_test_table": source_test_table,
        "resolved_table": actual_table,
        "resolved_train_table": actual_train_table,
        "resolved_test_table": actual_test_table,
    }

    pipeline = TrainingPipeline(
        db_path=db_path,
        limit=0 if (v2_run_selection is not None or v2_channel_ids is not None or time_start or time_end or resolved_source_tables or logical_series or logical_multi_sources) else limit,
        train_ratio=train_ratio,
        progress_callback=report_progress,
    )

    result = pipeline.run(
        algorithm=algorithm,
        table=actual_table,
        train_table=actual_train_table,
        test_table=actual_test_table,
        algorithm_params=algorithm_params,
        strategy=training_strategy,
        preloaded_detector=preloaded_detector,
        persist_model=persist_model,
        data_contract=data_contract,
    )

    effective_contract = dict(result.data_contract or data_contract)

    return {
        "status": "success",
        "experiment_id": result.experiment_id,
        "model_info": result.model_info or {},
        "config": {
            "algorithm_params": algorithm_params,
            "data_contract": effective_contract,
        },
        "_input_snapshot": result.input_snapshot,
        "result": {
            "input_evidence": result.input_evidence,
            "rows": [
                {
                    "timestamp": row.get("timestamp", ""),
                    "value": float(row["value"]),
                    **({"run_source_id":row.get("run_source_id"),"label_block_id":row.get("label_block_id",0)}
                       if row.get("run_source_id") is not None else {}),
                    **(
                        {"model_input_value": float(row["model_input_value"])}
                        if row.get("model_input_value") is not None else {}
                    ),
                    **({"label": row.get("label")} if "label" in row else {}),
                }
                for row in (result.result_rows or [])
                if isinstance(row, dict) and row.get("value") is not None
            ],
            # Backward-compatible key: historically named ``threshold`` even though
            # AlgorithmRunner exposes these values with anomaly-score semantics.
            "threshold": result.threshold,
            "decision_mode": (
                result.algorithm_result.decision_mode
                if result.algorithm_result is not None
                else None
            ),
            "decision_threshold": (
                result.algorithm_result.decision_threshold
                if result.algorithm_result is not None
                else None
            ),
            "decision_thresholds": (
                result.algorithm_result.decision_thresholds
                if result.algorithm_result is not None
                else []
            ),
            "pred_labels": (
                result.algorithm_result.pred_labels
                if result.algorithm_result is not None
                else []
            ),
            "algorithm": result.algorithm,
            "mode": result.data_mode or (
                "train_test_split"
                if (actual_train_table and actual_test_table) or train_ratio < 1.0
                else "simple"
            ),
            "feature_columns": list(features),
            "time_selection": data_contract.get("time_selection"),
            "input_view": "clean" if use_clean_data else "raw",
            "input_scaling": effective_contract.get("model_input_scaling", {}),
            "metrics": result.metrics.to_dict(),
        },
    }

