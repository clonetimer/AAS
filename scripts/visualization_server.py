#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web" / "visualization"

# ``python scripts/visualization_server.py`` 会把 scripts/ 放到 sys.path[0]。
# 项目同时存在根 ``core`` 与 ``scripts/core``，因此必须无条件把项目根提升到
# 最前面；仅在“ROOT 不存在”时插入会在 pytest / 嵌入式导入场景再次被
# scripts/core 遮蔽。
CORE_DIR = Path(__file__).resolve().parent
root_text = str(ROOT)
while root_text in sys.path:
    sys.path.remove(root_text)
sys.path.insert(0, root_text)

from core.path_policy import ManagedPathError, resolve_managed_file
# Compatibility re-exports for legacy imports of visualization_server.
# Do not remove without reviewing external plugins and scripts.
from detectors.registry import (  # pylint: disable=unused-import
    get_all_detector_schemas,  # pylint: disable=unused-import
    get_available_algorithms,  # pylint: disable=unused-import
    get_detector_config_schema,  # pylint: disable=unused-import
)
from detectors.dependencies import (  # pylint: disable=unused-import
    check_all_detectors,  # pylint: disable=unused-import
    install_all_missing_dependencies,  # pylint: disable=unused-import
    install_detector_dependencies,  # pylint: disable=unused-import
)

from scripts.core.model_manager import (
    save_model as core_save_model,
    load_model as core_load_model,
    init_models_dir,
    validate_saved_model as core_validate_saved_model,
    load_model_config as core_load_model_config,
)
from scripts.core.training import TrainingPipeline

# 导入 HTTP 处理器（已拆分到独立模块）
from scripts.server.v2_database_config import active_name
from scripts.server.handlers import DashboardHandler
from scripts.server.application_context import context_from_runtime, bind_http_context
from scripts.server.home_auth import HomeAuth
from scripts.server.home_stats import snapshot as home_snapshot
from scripts.server.execution_provenance import ExecutionRegistry
from utils.task_resources import TaskResourceTracker
from scripts.server import series_catalog as series_catalog
from scripts.server import model_registry_runtime as _model_registry_runtime
from scripts.server import legacy_simulation_api as _legacy_simulation_api
from scripts.server import legacy_catalog_api as _legacy_catalog_api
from scripts.server import legacy_preprocessing_api as _legacy_preprocessing_api
from scripts.server import service_algorithms as _service_algorithms

HOME_AUTH = HomeAuth(ROOT)

def home_dashboard_snapshot():
    """Current, read-only asset counts for the authenticated home page."""
    return home_snapshot(ROOT, TRAINING_TASKS, TASKS_LOCK)

from scripts.server.sqlite_guard import (
    busy_message as simulation_busy_message,
    replace_with_retry as simulation_replace_with_retry,
    simulation_db_locked,
    sqlite_connection as guarded_sqlite_connection,
    unlink_with_retry as simulation_unlink_with_retry,
)

# 初始化模型目录
init_models_dir(ROOT)

# Task state is owned by an explicit runtime object; these exports preserve
# older plugins/tests that still access the module's mutable attributes.
from scripts.server.task_runtime import TaskRuntime
TASK_RUNTIME = TaskRuntime.for_root(ROOT)
TRAINING_TASKS = TASK_RUNTIME.tasks
TASK_RESOURCE_TRACKER = TASK_RUNTIME.resource_tracker
EXECUTION_REGISTRY = TASK_RUNTIME.execution_registry
TASKS_LOCK = TASK_RUNTIME.lock

def _legacy_task_runtime() -> TaskRuntime:
    """Respect legacy monkeypatching of module-level task dependencies."""
    return TaskRuntime(tasks=TRAINING_TASKS, lock=TASKS_LOCK,
        resource_tracker=TASK_RESOURCE_TRACKER, execution_registry=EXECUTION_REGISTRY,
        state_dir=TASK_RUNTIME.state_dir, cancel_events=TASK_RUNTIME.cancel_events)

def bind_task_thread(task_id):
    return _legacy_task_runtime().bind_task_thread(task_id)

def update_task_progress(task_id, progress, message=None, stage=None):
    return _legacy_task_runtime().update_task_progress(task_id, progress, message, stage)

def create_task(task_id, task_type, config=None):
    return _legacy_task_runtime().create_task(task_id, task_type, config)

def create_training_task(task_id, config, task_type='training'):
    return _legacy_task_runtime().create_training_task(task_id, config, task_type=task_type)

def request_cancel_task(task_id):
    return _legacy_task_runtime().request_cancel_task(task_id)

def is_cancel_requested(task_id):
    return _legacy_task_runtime().is_cancel_requested(task_id)

def set_task_cancellable(task_id, cancellable, reason=None):
    return _legacy_task_runtime().set_task_cancellable(task_id, cancellable, reason)

def complete_task(task_id, result, error=None, failure=None, *, cancelled=False):
    # TASK_RESOURCE_TRACKER.finish is delegated into TaskRuntime.complete_task.
    return _legacy_task_runtime().complete_task(task_id, result, error, failure, cancelled=cancelled)

def get_task_status(task_id):
    return _legacy_task_runtime().get_task_status(task_id)

def get_public_task_status(task_id):
    return _legacy_task_runtime().get_public_task_status(task_id)

def list_public_task_statuses(limit=50, include_all=False):
    return _legacy_task_runtime().list_public_task_statuses(limit, include_all=include_all)

def count_public_task_statuses(include_all=False):
    return _legacy_task_runtime().count_public_task_statuses(include_all=include_all)

def setup_logging(config_path: str = "config.yaml") -> dict:
    """配置日志系统。"""
    config = {}
    config_path = ROOT / config_path

    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            log_config = config.get('logging', {})
            log_level = log_config.get('level', 'INFO').upper()
            log_file = log_config.get('file', 'logs/aas.log')

            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            logger = logging.getLogger()
            logger.setLevel(getattr(logging, log_level))

            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

            # 控制台输出
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

            # 文件输出（支持日志轮转）
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding='utf-8-sig'
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            logging.info("日志系统已配置：级别=%s, 文件=%s", log_level, log_file)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.basicConfig(level=logging.INFO)
            logging.warning("配置文件加载失败，使用默认日志配置: %s", e)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logging.info("配置文件不存在，使用默认日志配置")

    return config
SIMULATION_DB_PATH = ROOT / "datasets" / "simulation.db"
HISTORY_SAMPLE_DB_PATH = ROOT / "datasets" / "history_sample.db"
MODELS_DIR = ROOT / "models"
MODEL_DB_PATH = MODELS_DIR / "model.db"

from scripts.server.model_registry_service import ModelRegistryService
MODEL_REGISTRY = ModelRegistryService(ROOT, model_dir=MODELS_DIR, model_db_path=MODEL_DB_PATH,
    validator=core_validate_saved_model, json_reader=lambda path: read_json(path))

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.orchestrator import EvaluationOrchestrator


def ensure_model_db():
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.ensure_model_db(sys.modules[__name__])


def model_to_dict(row):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.model_to_dict(sys.modules[__name__], row)


def get_models(status=None, algorithm=None, limit=50):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.get_models(sys.modules[__name__], status, algorithm, limit)


def create_model(experiment_id, model_name, model_desc, algorithm, dataset, table_name, file_path, metrics, config):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.create_model(sys.modules[__name__], experiment_id, model_name, model_desc, algorithm, dataset, table_name, file_path, metrics, config)


def get_model(model_id):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.get_model(sys.modules[__name__], model_id)


def get_model_by_experiment_id(experiment_id):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.get_model_by_experiment_id(sys.modules[__name__], experiment_id)


def _training_model_dir(training_info):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime._training_model_dir(sys.modules[__name__], training_info)


def _training_artifact_metadata(training_info):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime._training_artifact_metadata(sys.modules[__name__], training_info)


def _mark_training_summary_published(summary_path, model_name, model_id=None):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime._mark_training_summary_published(sys.modules[__name__], summary_path, model_name, model_id)


def publish_training_experiment(relative_summary_path, model_name, model_desc=''):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.publish_training_experiment(sys.modules[__name__], relative_summary_path, model_name, model_desc)


def update_model_evaluation(model_id):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.update_model_evaluation(sys.modules[__name__], model_id)


def delete_model(model_id):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.delete_model(sys.modules[__name__], model_id)


def update_model_status(model_id, status):
    """Compatibility facade; logic in model_registry_runtime."""
    return _model_registry_runtime.update_model_status(sys.modules[__name__], model_id, status)


ALLOWED_MODEL_STATUSES = frozenset({"draft", "published", "evaluated"})



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the local visualization dashboard.")
    parser.add_argument("--host", default=None, help="Host to bind (overrides config.yaml)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (overrides config.yaml)")
    return parser.parse_args()


def json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except (ConnectionAbortedError, BrokenPipeError):
        pass
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"[JSON错误] 序列化失败：{e}")
        print(f"[JSON错误] payload类型：{type(payload)}")
        if isinstance(payload, dict):
            for key, val in payload.items():
                print(f"[JSON错误]   {key}: {type(val)}")
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def console_log(message: str) -> None:
    """Print terminal messages without crashing on non-UTF8 Windows consoles."""
    try:
        print(message)
    except UnicodeEncodeError:
        sanitized = (
            message.replace("✅", "[OK]")
            .replace("❌", "[ERROR]")
            .replace("📦", "[MODEL]")
            .replace("📊", "[REPORT]")
        )
        print(sanitized)


def parse_multipart_form(body: bytes, boundary: str) -> dict[str, str]:
    """Parse multipart form data."""
    form_data = {}
    parts = body.split(b'--' + boundary.encode())

    for part in parts:
        if part.strip() in [b'', b'--', b'--\r\n']:
            continue

        # Split headers and content
        lines = part.split(b'\r\n\r\n', 1)
        if len(lines) != 2:
            continue

        headers = lines[0].decode('utf-8')
        content = lines[1]

        # Extract field name and filename from Content-Disposition
        field_name = None
        filename = None
        for line in headers.split('\r\n'):
            if 'Content-Disposition' in line:
                # Extract field name
                if 'name="' in line:
                    name_start = line.find('name="') + 6
                    name_end = line.find('"', name_start)
                    field_name = line[name_start:name_end]

                # Extract filename
                if 'filename="' in line:
                    filename_start = line.find('filename="') + 10
                    filename_end = line.find('"', filename_start)
                    filename = line[filename_start:filename_end]

        # Store content (decode to string for non-file fields)
        if field_name:
            if filename:
                # 如果是文件字段，使用 field_name 作为键
                form_data[field_name] = content.rstrip(b'\r\n--')
                form_data[f'{field_name}_filename'] = filename
            else:
                # Decode text fields
                form_data[field_name] = content.rstrip(b'\r\n--').decode('utf-8')

    return form_data


def list_databases():
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.list_databases(sys.modules[__name__])


def resolve_managed_database_path(requested: str | Path, *, must_exist: bool=True):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.resolve_managed_database_path(sys.modules[__name__], requested, must_exist=must_exist)


def resolve_managed_backup_file(requested: str | Path, *, must_exist: bool=True):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.resolve_managed_backup_file(sys.modules[__name__], requested, must_exist=must_exist)


def resolve_managed_model_dir(requested: str | Path, *, must_exist: bool=True):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.resolve_managed_model_dir(sys.modules[__name__], requested, must_exist=must_exist)


def resolve_managed_model_file(model_dir: str | Path, filename: str):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.resolve_managed_model_file(sys.modules[__name__], model_dir, filename)


def _resolve_experiment_config_path(requested: str | Path, *, must_exist: bool=True):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api._resolve_experiment_config_path(sys.modules[__name__], requested, must_exist=must_exist)


def load_experiment_config(config_path: str | Path):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.load_experiment_config(sys.modules[__name__], config_path)


def list_experiment_configs():
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.list_experiment_configs(sys.modules[__name__])


def list_experiment_summaries(experiment_type=None):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.list_experiment_summaries(sys.modules[__name__], experiment_type)


def get_experiment_details(relative_summary_path: str):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.get_experiment_details(sys.modules[__name__], relative_summary_path)


def sqlite_tables(db_path: Path, model=None, subsystem=None, device=None, variable=None):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_tables(sys.modules[__name__], db_path, model, subsystem, device, variable)


def sqlite_rows(db_path: Path, table_name: str, limit: int=200, offset: int=0, time_col: str | None=None, start_time: Any | None=None, end_time: Any | None=None):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_rows(sys.modules[__name__], db_path, table_name, limit, offset, time_col, start_time, end_time)


def sqlite_continuous_series(db_path: Path, identity: dict[str, str], *, start_time=None, end_time=None, limit=400, offset=0, role=None):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_continuous_series(sys.modules[__name__], db_path, identity, start_time=start_time, end_time=end_time, limit=limit, offset=offset, role=role)


def sqlite_time_range(db_path: Path, table_name: str, time_col: str):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_time_range(sys.modules[__name__], db_path, table_name, time_col)


def sqlite_dataset_meta(db_path: Path):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_dataset_meta(sys.modules[__name__], db_path)


def sqlite_device_index(db_path: Path):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_device_index(sys.modules[__name__], db_path)


def sqlite_hierarchy_options(db_path: Path, model=None, subsystem=None, device=None):
    """Compatibility façade for scripts.server.legacy_catalog_api."""
    return _legacy_catalog_api.sqlite_hierarchy_options(sys.modules[__name__], db_path, model, subsystem, device)





# ==================== 数据预处理管线 ====================

# 预处理算子（填充缺失/修复异常值/平滑/归一化）已统一到
# scripts.core.preproc_plugins（run_pipeline），此处不再保留副本。


def check_clean_table_status(db_path: Path, table_name: str) -> dict[str, Any]:
    """Compatibility façade for scripts.server.legacy_preprocessing_api."""
    return _legacy_preprocessing_api.check_clean_table_status(sys.modules[__name__], db_path, table_name)


def run_data_preprocessing(
    db_path: Path,
    table_name: str,
    pipeline_config: dict[str, Any]
) -> dict[str, Any]:
    """Compatibility façade for scripts.server.legacy_preprocessing_api."""
    return _legacy_preprocessing_api.run_data_preprocessing(sys.modules[__name__], db_path, table_name, pipeline_config)


def delete_clean_table(db_path: Path, table_name: str) -> dict[str, Any]:
    """Compatibility façade for scripts.server.legacy_preprocessing_api."""
    return _legacy_preprocessing_api.delete_clean_table(sys.modules[__name__], db_path, table_name)


def run_experiment(config_path: str | Path, runner: str | None = None) -> dict[str, Any]:
    resolved = _resolve_experiment_config_path(config_path)
    runtime_overrides = {"runner": runner} if runner else None
    summary = EvaluationOrchestrator().run_experiment(
        str(resolved), runtime_overrides=runtime_overrides
    )
    return summary


def _base_table_name(table_name: str | None) -> str | None:
    """Return the source-table name for a managed clean table."""
    if not table_name:
        return table_name
    return table_name[:-6] if table_name.endswith("_clean") else table_name


def _resolve_clean_table(
    db_path: Path,
    source_table: str,
    *,
    auto_preprocess: bool,
    pipeline_config: dict[str, Any],
) -> str:
    """Resolve a clean table without ever silently falling back to raw data."""
    source_table = _base_table_name(source_table) or ""
    status = check_clean_table_status(db_path, source_table)
    if status.get("status") == "valid":
        return str(status.get("clean_table") or f"{source_table}_clean")
    if status.get("status") in {"invalidated", "not_exist"}:
        if not auto_preprocess:
            raise RuntimeError(
                f"模型要求清洁数据，但清洁表不可用（{status.get('status')}）："
                f"{source_table}_clean。请按训练时相同的预处理配置重新生成，或重新训练模型。"
            )
        result = run_data_preprocessing(db_path, source_table, pipeline_config)
        return str(result.get("clean_table") or f"{source_table}_clean")
    raise RuntimeError(status.get("message") or f"无法解析清洁表：{source_table}")


def run_evaluation(dataset: str, table: str, algorithm: str,
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
                   **kwargs) -> dict[str, Any]:
    """Stable public API delegating to the isolated evaluation workflow."""
    from scripts.server.evaluation_workflow import run_evaluation as _evaluate
    import sys as _sys
    params = locals().copy()
    extra = params.pop('kwargs', {})
    return _evaluate(_sys.modules[__name__], **params, **extra)


def save_model(*args, **kwargs):
    """兼容接口：委托给 core.model_manager.save_model"""
    return core_save_model(*args, **kwargs)


def load_model(*args, **kwargs):
    """兼容接口：委托给 core.model_manager.load_model"""
    return core_load_model(*args, **kwargs)


def load_model_config(*args, **kwargs):
    """读取模型训练时持久化的数据/算法配置。"""
    return core_load_model_config(*args, **kwargs)


def preview_import(file_content: str, file_type: str, filename: str = "") -> dict[str, Any]:
    """Compatibility wrapper for import preview + automatic column recognition."""
    from scripts.server.services import preview_import as service_preview_import

    return service_preview_import(file_content, file_type, filename=filename)


def import_data(
    file_content: str,
    file_type: str,
    target_db: str,
    table_prefix: str,
    table_name: str,
    field_mappings: dict | None = None,
    data_role: str = "auto",
    source_file: str = "",
    fill_missing: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper for the canonical import service."""
    from scripts.server.services import import_data as service_import_data

    return service_import_data(
        file_content, file_type, target_db, table_prefix, table_name,
        field_mappings, data_role=data_role, source_file=source_file, fill_missing=fill_missing,
    )


def import_data_multi(
    file_content: str,
    file_type: str,
    target_db: str,
    jobs: list[dict[str, Any]],
    *,
    source_file: str = "",
    fill_missing: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper for one-file/multi-table batch import."""
    from scripts.server.services import import_data_multi as service_import_data_multi

    return service_import_data_multi(
        file_content, file_type, target_db, jobs, source_file=source_file,
        fill_missing=fill_missing,
    )


def delete_history_table(db_path: Path, table_name: str) -> dict[str, Any]:
    """Compatibility wrapper for deleting a browsable historical data table."""
    from scripts.server.services import delete_history_table as service_delete_history_table

    return service_delete_history_table(db_path, table_name)


def preview_algorithm(file_content: str) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.preview_algorithm(file_content)


def validate_detector_interface(config: dict) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.validate_detector_interface(config)

def import_algorithm(file_content: bytes, filename: str, mode: str) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.import_algorithm(file_content, filename, mode, project_root=ROOT)


def import_from_zip(zip_content: bytes, filename: str, detectors_dir: Path) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.import_from_zip(zip_content, filename, detectors_dir, project_root=Path(globals().get("PROJECT_ROOT") or ROOT))

def import_code_only(code_content: bytes, filename: str, detectors_dir: Path) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.import_code_only(code_content, filename, detectors_dir, project_root=ROOT)


def import_config_only(file_content: str | bytes, filename: str, detectors_dir: Path) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.import_config_only(file_content, filename, detectors_dir, project_root=ROOT)


# ==================== 模拟数据库功能 ====================

def _remove_simulation_sidecars():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api._remove_simulation_sidecars(sys.modules[__name__])


@simulation_db_locked
def _simulation_table_counts():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api._simulation_table_counts(sys.modules[__name__])


def _is_algorithm_split_table(table_name: str):
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api._is_algorithm_split_table(sys.modules[__name__], table_name)


def init_simulation_db(force: bool=False):
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.init_simulation_db(sys.modules[__name__], force)


@simulation_db_locked
def delete_simulation_db():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.delete_simulation_db(sys.modules[__name__])


def sync_simulation_db():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.sync_simulation_db(sys.modules[__name__])


@simulation_db_locked
def get_simulation_status():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.get_simulation_status(sys.modules[__name__])


def get_simulation_fault_types():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.get_simulation_fault_types(sys.modules[__name__])


@simulation_db_locked
def get_simulation_tables():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.get_simulation_tables(sys.modules[__name__])


@simulation_db_locked
def get_fault_tables():
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.get_fault_tables(sys.modules[__name__])


@simulation_db_locked
def delete_fault_table(table_name: str):
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.delete_fault_table(sys.modules[__name__], table_name)


def preview_fault_injection(db_path: str, table_name: str, fault_type: str, config: dict[str, Any], limit: int=1000):
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.preview_fault_injection(sys.modules[__name__], db_path, table_name, fault_type, config, limit)


def execute_fault_injection(table_name: str, fault_type: str, config: dict[str, Any]):
    """Compatibility facade; logic in legacy_simulation_api."""
    return _legacy_simulation_api.execute_fault_injection(sys.modules[__name__], table_name, fault_type, config)




def import_algorithm_separate(
    config_file: bytes,
    config_filename: str,
    code_file: bytes,
    code_filename: str,
) -> dict[str, Any]:
    """Compatibility façade for scripts.server.service_algorithms."""
    return _service_algorithms.import_algorithm_separate(config_file, config_filename, code_file, code_filename, project_root=ROOT)


def main() -> int:
    config = setup_logging()

    args = parse_args()

    # 显式命令行参数优先；未提供时再使用配置文件。
    server_config = config.get('server', {})
    port = args.port if args.port is not None else int(server_config.get('port', 8765))
    host = args.host if args.host is not None else str(server_config.get('host', '127.0.0.1'))

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    # The live request path uses one explicit binding, never sys.modules lookup.
    bind_http_context(server, context_from_runtime(sys.modules[__name__]))
    logging.info("Visualization dashboard running at http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
