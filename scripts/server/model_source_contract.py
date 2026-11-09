"""Resolve model training channels for safe evaluation-table selection.

IDs are scoped to their source database and must never be compared across DBs.
Only complete four-level identities may be considered an automatic match.
"""
from __future__ import annotations

from typing import Any

FIELDS = ('model', 'subsystem', 'device', 'variable')


def _identity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or any(not str(value.get(field) or '').strip()
                                            for field in FIELDS):
        return None
    return {field: str(value[field]) for field in FIELDS}


def model_input_sources(main: Any, model: dict[str, Any]) -> dict[str, Any]:
    """Return training-order identities; never guess identities from table names."""
    saved = main.load_model_config(str(main.resolve_managed_model_dir(model['file_path'])))
    inner = saved.get('config') if isinstance(saved, dict) else None
    contract = inner.get('data_contract') if isinstance(inner, dict) else None
    contract = contract if isinstance(contract, dict) else {}
    logical_multi = contract.get('logical_multi_sources') or []
    if logical_multi:
        channels = [{'table': (item.get('tables') or [''])[0],
                     'identity': _identity(item.get('identity'))} for item in logical_multi]
        return {'model_id': model['id'], 'sources': channels,
                'complete': all(item['table'] and item['identity'] for item in channels),
                'data_view': 'raw', 'selection_mode': 'logical_multi'}
    source_tables = contract.get('source_tables') or []
    if not isinstance(source_tables, list):
        source_tables = []
    if not source_tables:
        source_tables = [contract.get('source_table') or model.get('table_name')]
    source_tables = [str(name) for name in source_tables if name]
    signatures = (contract.get('alignment') or {}).get('source_signatures') or {}
    signatures = signatures if isinstance(signatures, dict) else {}
    source_db = str(model.get('dataset') or saved.get('dataset') or '')
    catalog = {}
    if source_db:
        try:
            catalog = {item['name']: item for item in
                       main.sqlite_tables(main.resolve_managed_database_path(source_db))}
        except (FileNotFoundError, ValueError, OSError):
            # Old models can outlive their original training database. In that
            # case return incomplete identities, never a misleading match.
            pass
    sources = []
    for name in source_tables:
        identity = _identity(signatures.get(name))
        if identity is None:
            matches = catalog.get(name, {}).get('hierarchy') or []
            if len(matches) == 1:
                identity = _identity(matches[0])
        sources.append({'table': name, 'identity': identity})
    return {'model_id': model['id'], 'sources': sources,
            'selection_mode': 'logical_single' if contract.get('logical_series') else 'physical',
            'complete': bool(sources) and all(item['identity'] for item in sources),
            'data_view': 'clean' if contract.get('use_clean_data') else 'raw'}
