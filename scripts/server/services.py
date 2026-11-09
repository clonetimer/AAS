"""Public compatibility façade for AAS business services.

New code should depend on narrow ``service_*`` domain modules. External callers
can keep ``from scripts.server import services`` unchanged. Runtime-managed path
overrides are read from this module by the domain services.
"""
from __future__ import annotations
from .service_shared import *  # noqa: F401,F403
from .service_models import (
    read_json,
    ensure_model_db,
    model_to_dict,
    get_models,
    create_model,
    get_model,
    get_model_by_experiment_id,
    _training_model_dir,
    _training_artifact_metadata,
    _mark_training_summary_published,
    publish_training_experiment,
    update_model_evaluation,
    delete_model,
    update_model_status,
)
from .service_metadata import (
    _TABLE_META_NAME,
    _ROLE_LABELS,
    _coerce_binary_label,
    _ensure_table_meta,
    _infer_role_from_counts,
    _infer_role_from_name,
    _label_counts_from_rows,
    _load_data_import_recognition_config,
    _normalize_label_token,
    _normalized_column_name,
    _quote_identifier,
    _reset_data_import_recognition_cache,
    _save_table_role,
    _table_role_info,
)
from .service_ingestion import (
    _parse_import_rows,
    _find_alias,
    _sample_is_numeric,
    _dataset_tag,
    _suggest_import_mapping,
    preview_import,
    _is_numeric_feature,
    _apply_field_mappings,
    _fill_missing_measurement_values,
    _validate_import_role,
    _import_rows_into_connection,
    import_data_multi,
    import_data,
)
from .service_catalog import (
    list_databases,
    list_experiment_configs,
    list_experiment_summaries,
    get_experiment_details,
    sqlite_tables,
    delete_history_table,
    sqlite_rows,
    sqlite_time_range,
    sqlite_continuous_series,
    sqlite_dataset_meta,
    sqlite_device_index,
    sqlite_hierarchy_options,
)
from .service_algorithms import (
    preview_algorithm,
    validate_detector_interface,
    import_algorithm,
    import_from_zip,
    import_code_only,
    import_config_only,
    import_algorithm_separate,
)
from .service_simulation import (
    _remove_simulation_sidecars,
    _simulation_table_counts,
    _is_algorithm_split_table,
    init_simulation_db,
    delete_simulation_db,
    sync_simulation_db,
    get_simulation_status,
    get_simulation_fault_types,
    get_simulation_tables,
    get_fault_tables,
    delete_fault_table,
    preview_fault_injection,
    execute_fault_injection,
)

# Deliberately no module-level dispatch or new SQLite state here.
