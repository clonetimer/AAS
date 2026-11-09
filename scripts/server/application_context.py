"""Explicit dependencies for a running HTTP server (Phase 37).

Only the startup boundary constructs this object.  The production request path
never resolves a main module via ``sys.modules``.  The operation list is
intentional: newly introduced handler dependencies require a reviewed addition.

Historical command-line callers still use visualization_server facades.  The
stored operation callables may invoke those legacy facades; moving their
remaining global state is subsequent work, *not* a claim of full isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable, Mapping
from functools import partial

from scripts.server.task_runtime import TaskRuntime
from scripts.server.model_registry_service import ModelRegistryService
from scripts.server.algorithm_process import AlgorithmProcessService


# Used by active HTTP handlers and their domain-specific adapters.
HTTP_OPERATIONS = frozenset({
    'bind_task_thread', 'check_all_detectors', 'check_clean_table_status',
    'complete_task', 'create_model', 'create_task', 'create_training_task', 'delete_history_table',
    'delete_model', 'get_all_detector_schemas', 'get_available_algorithms',
    'get_detector_config_schema', 'get_experiment_details', 'get_fault_tables',
    'get_model', 'get_models', 'get_simulation_fault_types',
    'get_simulation_status', 'get_simulation_tables', 'get_task_status',
    'get_public_task_status', 'list_public_task_statuses', 'count_public_task_statuses',
    'request_cancel_task', 'is_cancel_requested', 'set_task_cancellable',
    'home_dashboard_snapshot', 'import_algorithm_separate', 'import_data',
    'import_data_multi', 'install_all_missing_dependencies',
    'install_detector_dependencies', 'json_response', 'list_databases',
    'list_experiment_configs', 'list_experiment_summaries',
    'load_experiment_config', 'load_model', 'load_model_config',
    'parse_multipart_form', 'preview_algorithm', 'preview_import',
    'resolve_managed_database_path', 'resolve_managed_model_dir',
    'resolve_managed_model_file', 'run_evaluation', 'sqlite_continuous_series',
    'sqlite_dataset_meta', 'sqlite_device_index', 'sqlite_hierarchy_options',
    'sqlite_rows', 'sqlite_tables', 'sqlite_time_range',
    'update_model_evaluation', 'update_model_status', 'update_task_progress',
})


@dataclass(frozen=True)
class HttpApplicationContext:
    """Immutable binding of configuration, runtime state and approved APIs.

    Mutable task dictionaries/locks are intentionally *references*; binding
    freezes which state a server uses, not the state itself.
    """
    ROOT: Path
    STATIC_DIR: Path
    SIMULATION_DB_PATH: Path
    HOME_AUTH: Any
    EXECUTION_REGISTRY: Any
    TASK_RESOURCE_TRACKER: Any
    TRAINING_TASKS: dict
    TASKS_LOCK: Any
    TASK_RUNTIME: TaskRuntime
    MODEL_REGISTRY: ModelRegistryService
    ALGORITHM_SUPERVISOR: AlgorithmProcessService
    MODELS_DIR: Path
    MODEL_DB_PATH: Path
    _operations: Mapping[str, Callable[..., Any]] = field(repr=False)

    def __getattr__(self, name: str):
        try:
            return self._operations[name]
        except KeyError as exc:
            raise AttributeError(f'Unbound HTTP operation: {name}') from exc


def bind_http_context(server: Any, context: HttpApplicationContext) -> None:
    """Install the chosen application before starting the HTTP worker threads."""
    if getattr(server, 'aas_application', None) is not None:
        raise RuntimeError('HTTP application is already bound')
    server.aas_application = context


def context_from_runtime(runtime: Any) -> HttpApplicationContext:
    """Bind task and model services to one server at bootstrap time.

    The module-level compatibility functions remain for external code, but
    live HTTP task/model requests no longer call them or inspect sys.modules.
    """
    operations = {name: getattr(runtime, name) for name in sorted(HTTP_OPERATIONS)}
    if not all(callable(value) for value in operations.values()):
        raise TypeError('Every bound HTTP operation must be callable')

    tasks = TaskRuntime(
        tasks=runtime.TRAINING_TASKS,
        lock=runtime.TASKS_LOCK,
        resource_tracker=runtime.TASK_RESOURCE_TRACKER,
        execution_registry=runtime.EXECUTION_REGISTRY,
    )
    # Load task statuses before accepting requests. Running records from a
    # previous process are explicitly interrupted; never silently resumed.
    tasks.enable_recovery(Path(runtime.ROOT))
    model_registry = ModelRegistryService(
        root=Path(runtime.ROOT),
        model_dir=Path(runtime.MODELS_DIR),
        model_db_path=Path(runtime.MODEL_DB_PATH),
        validator=runtime.core_validate_saved_model,
        json_reader=runtime.read_json,
    )
    supervisor = AlgorithmProcessService(Path(runtime.ROOT), model_registry.MODELS_DIR)
    for name in ('bind_task_thread', 'update_task_progress',
                 'create_task', 'create_training_task', 'request_cancel_task',
                 'is_cancel_requested', 'set_task_cancellable', 'complete_task', 'get_task_status',
                 'get_public_task_status', 'list_public_task_statuses',
                 'count_public_task_statuses', 'record_terminal_task'):
        operations[name] = getattr(tasks, name)
    for name in ('create_model', 'get_models', 'get_model', 'delete_model',
                 'update_model_status', 'update_model_evaluation'):
        operations[name] = getattr(model_registry, name)
    # Route *all* model-path validation through the same bound store used by
    # publication. An HTTP request may never fall back to the main module's
    # default project directory when a scoped application is mounted.
    operations['resolve_managed_model_dir'] = model_registry.resolve_managed_model_dir
    operations['resolve_managed_model_file'] = model_registry.resolve_managed_model_file
    # Evaluation callbacks are bound to the same task store as the HTTP request;
    # otherwise a threaded training worker could update the global legacy store.
    from scripts.server.evaluation_workflow import run_evaluation as evaluate
    from scripts.server.legacy_catalog_api import resolve_managed_database_path as resolve_database
    evaluation_dependencies = SimpleNamespace(
        resolve_managed_database_path=partial(resolve_database, model_registry),
        _base_table_name=runtime._base_table_name,
        _resolve_clean_table=runtime._resolve_clean_table,
        active_name=runtime.active_name,
        # The live training pipeline writes to this application's model root,
        # not core.model_manager's process-wide compatibility default.
        TrainingPipeline=partial(runtime.TrainingPipeline, models_dir=model_registry.MODELS_DIR),
        update_task_progress=tasks.update_task_progress,
        console_log=runtime.console_log,
    )
    operations['run_evaluation'] = partial(evaluate, evaluation_dependencies)

    from scripts.server.home_stats import snapshot as home_snapshot
    operations['home_dashboard_snapshot'] = lambda: home_snapshot(
        model_registry.ROOT, tasks.tasks, tasks.lock)

    return HttpApplicationContext(
        ROOT=Path(runtime.ROOT), STATIC_DIR=Path(runtime.STATIC_DIR),
        SIMULATION_DB_PATH=Path(runtime.SIMULATION_DB_PATH),
        HOME_AUTH=runtime.HOME_AUTH, EXECUTION_REGISTRY=tasks.execution_registry,
        TASK_RESOURCE_TRACKER=tasks.resource_tracker,
        TRAINING_TASKS=tasks.tasks, TASKS_LOCK=tasks.lock,
        TASK_RUNTIME=tasks, MODEL_REGISTRY=model_registry,
        ALGORITHM_SUPERVISOR=supervisor,
        MODELS_DIR=model_registry.MODELS_DIR, MODEL_DB_PATH=model_registry.MODEL_DB_PATH,
        _operations=MappingProxyType(operations),
    )
