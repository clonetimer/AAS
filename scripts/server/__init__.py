"""Server package with lazy public exports.

Keeping imports lazy avoids duplicate ``server.*``/``scripts.server.*`` module
identities and prevents the large services module from loading just by importing
``scripts.server``.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_HANDLER_EXPORTS = {"DashboardHandler"}
_SERVICE_EXPORTS = {
    "model_to_dict", "get_models", "create_model", "get_model", "get_model_by_experiment_id",
    "publish_training_experiment", "update_model_evaluation", "delete_model", "update_model_status",
    "list_databases", "list_experiment_configs", "list_experiment_summaries",
    "get_experiment_details", "sqlite_tables", "sqlite_rows", "sqlite_time_range", "sqlite_continuous_series", "sqlite_dataset_meta",
    "sqlite_device_index", "sqlite_hierarchy_options",
    "preview_import", "import_data", "import_data_multi", "preview_algorithm", "validate_detector_interface",
    "import_algorithm", "import_from_zip", "import_code_only", "import_config_only",
    "init_simulation_db", "delete_simulation_db", "sync_simulation_db", "get_simulation_status",
    "get_simulation_tables", "get_fault_tables", "delete_fault_table",
    "preview_fault_injection", "execute_fault_injection", "import_algorithm_separate",
}

__all__ = sorted(_HANDLER_EXPORTS | _SERVICE_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _HANDLER_EXPORTS:
        return getattr(import_module(".handlers", __name__), name)
    if name in _SERVICE_EXPORTS:
        return getattr(import_module(".services", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
