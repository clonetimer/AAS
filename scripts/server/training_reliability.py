"""Failure-boundary helpers for the V2 asynchronous training workflow.

Only an unpublished, newly-created model directory whose name matches the
failed experiment may be removed. Persisted model registry rows are never
removed or modified as part of task error handling.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any


def cleanup_unpublished_artifact(app: Any, summary: dict | None) -> bool:
    """Best-effort cleanup when training fails after committing an artifact.

    Fail closed: if the application cannot establish the owner directory or
    consult its model registry, retain the artifact for manual inspection.
    This is process-level cleanup, not a cross-process publication transaction.
    """
    if not isinstance(summary, dict):
        return False
    experiment_id = summary.get('experiment_id')
    artifact = summary.get('model_info') or {}
    if not isinstance(experiment_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', experiment_id):
        return False
    if not isinstance(artifact, dict) or not artifact.get('model_dir'):
        return False
    registry = getattr(app, 'MODEL_REGISTRY', None)
    if registry is None or not callable(getattr(registry, 'get_model_by_experiment_id', None)):
        return False
    target = Path(artifact['model_dir'])
    root = Path(app.MODELS_DIR).resolve()
    if target.is_symlink() or target.name != f'model_{experiment_id}' or target.resolve() != root / target.name:
        return False
    try:
        if registry.get_model_by_experiment_id(experiment_id) is not None:
            return False
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True
    except (OSError, ValueError, TypeError):
        logging.exception('Could not clean unpublished failed artifact for experiment %s', experiment_id)
        return False
