"""Database catalog, managed file paths, and experiment query helpers.

Explicit application context preserves runtime path overrides, without cyclic imports.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any
import yaml

def list_databases(app) -> list[dict[str, Any]]:
    databases = []
    for db_path in sorted((app.ROOT / 'datasets').glob('*.db')):
        try:
            tables = app.sqlite_tables(db_path)
        except Exception as e:
            databases.append({'label': db_path.name, 'value': db_path.relative_to(app.ROOT).as_posix(), 'size_mb': round(db_path.stat().st_size / (1024 * 1024), 2), 'table_count': 0, 'preferred_table': None, 'error': str(e)})
            continue
        preferred_table = next((item['name'] for item in tables if item['name'] == 'history_data'), tables[0]['name'] if tables else None)
        has_required_fields = False
        for table in tables:
            if 'model' in table.get('columns', []) or 'subsystem' in table.get('columns', []):
                has_required_fields = True
                break
        databases.append({'label': db_path.name, 'value': db_path.relative_to(app.ROOT).as_posix(), 'size_mb': round(db_path.stat().st_size / (1024 * 1024), 2), 'table_count': len(tables), 'preferred_table': preferred_table, 'has_required_fields': has_required_fields})
    databases.sort(key=lambda item: (not item.get('has_required_fields', False), item['label']))
    return databases

def resolve_managed_database_path(app, requested: str | Path, *, must_exist: bool=True) -> Path:
    """Resolve a database reference strictly inside the project datasets area.

    API callers may use either the canonical ``datasets/name.db`` reference or
    a bare database filename.  Absolute paths and parent traversal remain
    forbidden.
    """
    raw = Path(str(requested or '').strip())
    logical = raw
    if str(raw) and (not raw.is_absolute()) and (len(raw.parts) == 1):
        logical = Path('datasets') / raw
    return app.resolve_managed_file(app.ROOT / 'datasets', logical, anchor=app.ROOT, allowed_suffixes=('.db', '.sqlite', '.sqlite3'), must_exist=must_exist)

def resolve_managed_backup_file(app, requested: str | Path, *, must_exist: bool=True) -> Path:
    """Resolve a backup database strictly inside the project ``backups/`` area."""
    raw = Path(str(requested or '').strip())
    logical = raw
    if str(raw) and (not raw.is_absolute()) and (len(raw.parts) == 1):
        logical = Path('backups') / raw
    return app.resolve_managed_file(app.ROOT / 'backups', logical, anchor=app.ROOT, allowed_suffixes=('.db', '.sqlite', '.sqlite3'), must_exist=must_exist)

def resolve_managed_model_dir(app, requested: str | Path, *, must_exist: bool=True) -> Path:
    """Resolve a model directory strictly inside ``models/``."""
    models_root = Path(getattr(app, 'MODELS_DIR', app.ROOT / 'models')).resolve()
    raw = Path(str(requested or '').strip())
    if not str(raw):
        raise app.ManagedPathError('model directory is required')
    candidate = (raw if raw.is_absolute() else app.ROOT / raw).resolve()
    try:
        candidate.relative_to(models_root)
    except ValueError as exc:
        raise app.ManagedPathError('path must stay under managed root: models') from exc
    if candidate.is_file():
        candidate = candidate.parent
    if candidate == models_root:
        raise app.ManagedPathError('model directory must be a child of managed root: models')
    if must_exist and (not candidate.is_dir()):
        raise FileNotFoundError(f'model directory does not exist: {requested}')
    return candidate

def resolve_managed_model_file(app, model_dir: str | Path, filename: str) -> Path:
    """Resolve a JSON metadata file below one managed model directory."""
    directory = app.resolve_managed_model_dir(model_dir)
    name = str(filename or '').strip()
    if not name or Path(name).name != name:
        raise app.ManagedPathError('model filename must be a plain filename')
    candidate = (directory / name).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError as exc:
        raise app.ManagedPathError('model file must stay under selected model directory') from exc
    if candidate.suffix.lower() != '.json':
        raise app.ManagedPathError('only JSON model metadata files may be read')
    if not candidate.is_file():
        raise FileNotFoundError(f'model file does not exist: {name}')
    return candidate

def _resolve_experiment_config_path(app, requested: str | Path, *, must_exist: bool=True) -> Path:
    try:
        return app.resolve_managed_file(app.ROOT / 'configs' / 'experiments', requested, anchor=app.ROOT, allowed_suffixes=('.yaml', '.yml'), must_exist=must_exist)
    except app.ManagedPathError as exc:
        raise app.ManagedPathError('experiment config must stay under configs/experiments') from exc

def load_experiment_config(app, config_path: str | Path) -> dict[str, Any]:
    """Load a host-managed experiment YAML file."""
    path = app._resolve_experiment_config_path(config_path)
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('实验配置必须是 YAML 对象')
    return payload

def list_experiment_configs(app) -> list[dict[str, str]]:
    configs = []
    for config_path in sorted((app.ROOT / 'configs' / 'experiments').glob('*.yaml')):
        configs.append({'label': config_path.name, 'value': config_path.relative_to(app.ROOT).as_posix()})
    return configs

def list_experiment_summaries(app, experiment_type=None) -> list[dict[str, Any]]:
    summaries = []
    for summary_path in sorted((app.ROOT / 'outputs').glob('*/reports/summary.json')):
        try:
            summary = app.read_json(summary_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        exp_info = summary.get('experiment', {})
        if not isinstance(exp_info, dict) or not exp_info:
            exp_info = {'experiment_id': summary.get('experiment_id', summary_path.parent.parent.name), 'type': 'evaluation', 'run_dir': str(summary_path.parent.parent)}
        exp_type = exp_info.get('type', 'evaluation')
        if experiment_type and exp_type != experiment_type:
            continue
        results = summary.get('results', [])
        detectors = [item.get('detector', '') for item in results if isinstance(item, dict)]
        success_count = sum((1 for item in results if isinstance(item, dict) and (item.get('status') == 'success' or (isinstance(item.get('raw_result'), dict) and item['raw_result'].get('status') == 'success'))))
        metric_digest = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            metrics = item.get('metrics', {})
            if isinstance(metrics, dict):
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        metric_digest.setdefault(key, []).append(float(value))
        metric_avg = {k: round(sum(v) / len(v), 6) for k, v in metric_digest.items() if v}
        summary_record = {'experiment_id': exp_info.get('experiment_id', summary_path.parent.parent.name), 'run_dir': exp_info.get('run_dir', str(summary_path.parent.parent)), 'detectors': detectors, 'detector_count': len(detectors), 'success_count': success_count, 'result_count': len(results), 'metrics_avg': metric_avg, 'path': summary_path.relative_to(app.ROOT).as_posix(), 'type': exp_type}
        if exp_type == 'training':
            training_info = summary.get('training', {})
            summary_record['algorithm'] = training_info.get('algorithm')
            summary_record['dataset'] = training_info.get('dataset')
            summary_record['table'] = training_info.get('table')
            summary_record['model_dir'] = training_info.get('model_dir')
            summary_record['model_file_path'] = training_info.get('model_file_path')
            model_dir, artifact = app._training_artifact_metadata(training_info)
            artifact_exp_id = str(artifact.get('experiment_id') or '').strip() if artifact else ''
            registry_model = app.get_model_by_experiment_id(artifact_exp_id) if artifact_exp_id else None
            summary_record['model_available'] = bool(model_dir and artifact)
            summary_record['model_artifact_experiment_id'] = artifact_exp_id or None
            summary_record['model_published'] = bool(registry_model) if artifact_exp_id else bool(training_info.get('model_published', False))
            summary_record['model_name'] = (registry_model or {}).get('model_name') if artifact_exp_id else training_info.get('model_name')
            summary_record['model_id'] = (registry_model or {}).get('id') if artifact_exp_id else training_info.get('model_id')
        elif exp_type == 'evaluation':
            eval_info = summary.get('evaluation', {})
            summary_record['model_id'] = eval_info.get('model_id')
            summary_record['model_name'] = eval_info.get('model_name')
            summary_record['model_algorithm'] = eval_info.get('model_algorithm')
            summary_record['eval_dataset'] = eval_info.get('eval_dataset')
            summary_record['eval_table'] = eval_info.get('eval_table')
            summary_record['evaluation_mode'] = eval_info.get('evaluation_mode', 'supervised')
            summary_record['ground_truth_available'] = eval_info.get('ground_truth_available', True)
        summaries.append(summary_record)
    summaries.sort(key=lambda item: item['experiment_id'], reverse=True)
    return summaries

def get_experiment_details(app, relative_summary_path: str) -> dict[str, Any]:
    summary_path = app.resolve_managed_file(app.ROOT / 'outputs', relative_summary_path, anchor=app.ROOT, required_name='summary.json', must_exist=True)
    payload = app.read_json(summary_path)
    experiment = payload.get('experiment', {}) if isinstance(payload, dict) else {}
    training = payload.get('training', {}) if isinstance(payload, dict) else {}
    evaluation = payload.get('evaluation', {}) if isinstance(payload, dict) else {}
    results = payload.get('results', []) if isinstance(payload, dict) else []
    rows = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_result = item.get('raw_result', {})
        metrics = item.get('metrics', {})
        rows.append({'detector': item.get('detector'), 'status': raw_result.get('status') if raw_result else item.get('status'), 'error_code': raw_result.get('error_code') if raw_result else item.get('error_code'), 'metrics': metrics, 'chart_trace': item.get('chart_trace') if isinstance(item.get('chart_trace'), dict) else None})
    return {'experiment': experiment, 'training': training, 'evaluation': evaluation, 'provenance': payload.get('provenance') if isinstance(payload, dict) else None, 'resource_metrics': app.TASK_RESOURCE_TRACKER.by_experiment(experiment.get('experiment_id')), 'rows': rows}

def sqlite_tables(app, db_path: Path, model=None, subsystem=None, device=None, variable=None) -> list[dict[str, Any]]:
    """Compatibility wrapper around the split server service layer."""
    from scripts.server.services import sqlite_tables as service_sqlite_tables
    return service_sqlite_tables(db_path, model, subsystem, device, variable)

def sqlite_rows(app, db_path: Path, table_name: str, limit: int=200, offset: int=0, time_col: str | None=None, start_time: Any | None=None, end_time: Any | None=None) -> dict[str, Any]:
    """Compatibility wrapper around the split server service layer."""
    from scripts.server.services import sqlite_rows as service_sqlite_rows
    return service_sqlite_rows(db_path, table_name, limit=limit, offset=offset, time_col=time_col, start_time=start_time, end_time=end_time)

def sqlite_continuous_series(app, db_path: Path, identity: dict[str, str], *, start_time=None, end_time=None, limit=400, offset=0, role=None) -> dict[str, Any]:
    """Compatibility adapter for GET /api/telemetry-series."""
    from scripts.server.services import sqlite_continuous_series as query
    return query(db_path, identity, start_time=start_time, end_time=end_time, limit=limit, offset=offset, role=role)

def sqlite_time_range(app, db_path: Path, table_name: str, time_col: str) -> dict[str, Any]:
    """Compatibility wrapper for full-table time range discovery."""
    from scripts.server.services import sqlite_time_range as service_sqlite_time_range
    return service_sqlite_time_range(db_path, table_name, time_col)

def sqlite_dataset_meta(app, db_path: Path) -> dict[str, str]:
    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        rows = conn.execute('SELECT key, value FROM dataset_meta').fetchall()
    return {str(key): str(value) for key, value in rows}

def sqlite_device_index(app, db_path: Path) -> dict[str, Any]:
    """Return device index information for both supported SQLite layouts.

    Older generated datasets contain ``device_table_index`` while the current
    history/simulation datasets expose ``table_index``.  The API keeps one
    stable response shape and derives a device-level view when only
    ``table_index`` is present.
    """
    with app.guarded_sqlite_connection(db_path, simulation_db_path=app.SIMULATION_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'device_table_index' in tables:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(device_table_index)').fetchall()]
            rows = [dict(row) for row in conn.execute('SELECT * FROM device_table_index ORDER BY device').fetchall()]
            return {'columns': columns, 'rows': rows}
        if 'table_index' in tables:
            columns = ['device', 'table_name', 'subsystem_count', 'variable_count', 'row_count', 'description']
            rows = [dict(row) for row in conn.execute("\n                    SELECT\n                        device,\n                        GROUP_CONCAT(table_name) AS table_name,\n                        COUNT(DISTINCT model || ':' || subsystem) AS subsystem_count,\n                        COUNT(*) AS variable_count,\n                        COALESCE(SUM(row_count), 0) AS row_count,\n                        NULL AS description\n                    FROM table_index\n                    GROUP BY device\n                    ORDER BY device\n                    ").fetchall()]
            return {'columns': columns, 'rows': rows}
    raise ValueError('database does not provide a supported device index table')

def sqlite_hierarchy_options(app, db_path: Path, model=None, subsystem=None, device=None) -> dict[str, Any]:
    """Compatibility facade over the same catalog-backed hierarchy API."""
    from scripts.server.services import sqlite_hierarchy_options as service_options
    return service_options(db_path, model, subsystem, device)
