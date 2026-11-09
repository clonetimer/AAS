"""Phase 38: model database paths and registry calls scoped to an application.

The canonical SQL implementation is in model_registry_runtime. This adapter
supplies paths, validation, and nested callbacks explicitly instead of handing
the registry a mutable global application module.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from core.path_policy import ManagedPathError, resolve_managed_file
from scripts.server import legacy_catalog_api, model_registry_runtime


_REGISTRY_FUNCTIONS = frozenset({
    'ensure_model_db', 'model_to_dict', 'get_models', 'create_model',
    'get_model', 'get_model_by_experiment_id', '_training_model_dir',
    '_training_artifact_metadata', '_mark_training_summary_published',
    'publish_training_experiment', 'update_model_evaluation',
    'delete_model', 'update_model_status',
})


class ModelRegistryService:
    """Registry facade whose storage directory is captured at startup."""

    ALLOWED_MODEL_STATUSES = frozenset({'draft', 'published', 'evaluated'})
    ManagedPathError = ManagedPathError
    resolve_managed_file = staticmethod(resolve_managed_file)

    def __init__(self, root: Path, *, model_dir: Path | None = None,
                 model_db_path: Path | None = None, validator=None, json_reader=None):
        self.ROOT = Path(root).resolve()
        def project_path(value: Path | None, default: Path) -> Path:
            candidate = Path(value) if value is not None else default
            return (candidate if candidate.is_absolute() else self.ROOT / candidate).resolve()
        self.MODELS_DIR = project_path(model_dir, self.ROOT / 'models')
        self.MODEL_DB_PATH = project_path(model_db_path, self.MODELS_DIR / 'model.db')
        try:
            self.MODELS_DIR.relative_to(self.ROOT)
        except ValueError as exc:
            raise ValueError('model directory must reside inside the application root') from exc
        if self.MODEL_DB_PATH.parent != self.MODELS_DIR:
            raise ValueError('model database must reside in its managed model directory')
        self.core_validate_saved_model = validator
        self.read_json = json_reader

    def __getattr__(self, name: str) -> Any:
        if name in _REGISTRY_FUNCTIONS:
            return partial(getattr(model_registry_runtime, name), self)
        raise AttributeError(name)

    def resolve_managed_model_dir(self, requested, *, must_exist=True):
        return legacy_catalog_api.resolve_managed_model_dir(self, requested, must_exist=must_exist)

    def resolve_managed_model_file(self, model_dir, filename):
        return legacy_catalog_api.resolve_managed_model_file(self, model_dir, filename)
