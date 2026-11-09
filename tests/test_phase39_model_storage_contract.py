"""Phase 39: enforce one scoped model artifact path across the live pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.core import model_manager
from scripts.core.model_manager import load_model, load_model_config, validate_saved_model
from scripts.core.training import TrainingPipeline
from scripts.server.application_context import context_from_runtime
from scripts.server.model_registry_service import ModelRegistryService
from tests.test_telemetry_v2_training_phase5b import build


class ConstantDetector:
    def __init__(self, score: int):
        self.constant = score

    def score(self, rows):
        return [self.constant for _ in rows]


def _registry(root: Path, folder: str = 'model-store') -> ModelRegistryService:
    return ModelRegistryService(root, model_dir=root / folder,
                                model_db_path=root / folder / 'model.db',
                                validator=validate_saved_model,
                                json_reader=lambda path: json.loads(Path(path).read_text('utf8')))


def test_real_train_publish_restore_and_evaluate_with_scoped_storage(tmp_path, monkeypatch):
    """A learned detector must survive registration and score without refit."""
    db = build(tmp_path)
    root = tmp_path / 'aas-app'
    root.mkdir()
    registry = _registry(root)
    monkeypatch.setattr(model_manager, '_MODELS_DIR', tmp_path / 'wrong-legacy-root')
    contract = {
        'version': 6, 'feature_columns': ['value', 'source_2'],
        'v2_channel_ids': [1, 2], 'v2_role': 'train',
        'v2_alignment': {'method': 'exact', 'tolerance_seconds': 0},
        'model_input_scaling': {'enabled': True, 'method': 'zscore'},
    }
    result = TrainingPipeline(db, limit=0, train_ratio=.7,
                              models_dir=registry.MODELS_DIR).run(
        'isolation_forest_detector', table='v2_channels',
        algorithm_params={'n_estimators': 12, 'contamination': .1},
        data_contract=contract,
    )
    model_dir = Path(result.model_info['model_dir']).resolve()
    assert model_dir.parent == registry.MODELS_DIR
    assert not (tmp_path / 'wrong-legacy-root').exists()
    assert (model_dir / 'model.pkl').is_file()
    saved = load_model_config(model_dir)
    assert saved['algorithm'] == 'isolation_forest_detector'
    assert saved['config']['data_contract']['model_input_scaling']['parameters']
    registry.create_model(result.experiment_id, 'trained detector', '',
                          'isolation_forest_detector', db.name, 'v2_channels',
                          model_dir.relative_to(root).as_posix(),
                          result.metrics.to_dict(), saved['config'])
    entry = registry.get_model_by_experiment_id(result.experiment_id)
    assert entry['file_path'] == model_dir.relative_to(root).as_posix()
    assert entry['status'] == 'published'
    restored = load_model(registry.resolve_managed_model_dir(entry['file_path']), entry['algorithm'])
    restored.fit = Mock(side_effect=AssertionError('evaluation may not retrain'))
    assessment = TrainingPipeline(db, limit=0, train_ratio=1.0,
                                  models_dir=registry.MODELS_DIR).run(
        'isolation_forest_detector', table='v2_channels',
        preloaded_detector=restored, persist_model=False,
        data_contract={**saved['config']['data_contract'], 'v2_role': 'test'},
    )
    restored.fit.assert_not_called()
    assert assessment.metrics.mode == 'supervised'
    assert assessment.metrics.total_samples == 20
    assert assessment.model_info == {}
    registry.update_model_evaluation(entry['id'])
    assert registry.get_model(entry['id'])['evaluation_count'] == 1
    assert registry.get_model(entry['id'])['status'] == 'evaluated'
    assert len(list(registry.MODELS_DIR.glob('model_*'))) == 1


def test_two_scoped_saves_with_identical_experiment_id_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(model_manager, '_MODELS_DIR', tmp_path / 'unused-default')
    for expected, name in enumerate(('a', 'b'), start=1):
        store = tmp_path / name / 'models'
        info = model_manager.save_model(
            'reused-id', 'three_sigma', 'v2.db', 'v2_channels',
            {'accuracy': 1.0}, detector=ConstantDetector(expected),
            models_dir=store,
        )
        assert Path(info['model_dir']).parent == store.resolve()
        assert (store / 'model_reused-id' / 'model.pkl').exists()
        assert load_model(info['model_dir'], 'three_sigma').score([0]) == [expected]
    assert not (tmp_path / 'unused-default').exists()


def test_managed_model_path_uses_custom_store_and_rejects_sibling(tmp_path):
    root = tmp_path / 'runtime'
    root.mkdir()
    reg = _registry(root, 'custom-models')
    valid = reg.MODELS_DIR / 'model_ok'
    valid.mkdir(parents=True)
    assert reg.resolve_managed_model_dir('custom-models/model_ok') == valid
    assert reg.resolve_managed_model_dir(valid) == valid
    with pytest.raises(reg.ManagedPathError):
        reg.resolve_managed_model_dir('models/model_ok', must_exist=False)
    with pytest.raises(reg.ManagedPathError):
        reg.resolve_managed_model_dir('../outside', must_exist=False)
    with pytest.raises(reg.ManagedPathError):
        reg.resolve_managed_model_dir('custom-models', must_exist=False)


def test_scoped_model_store_must_be_inside_app_root(tmp_path):
    with pytest.raises(ValueError, match='application root'):
        ModelRegistryService(tmp_path / 'app', model_dir=tmp_path / 'outside')
    with pytest.raises(ValueError, match='managed model directory'):
        ModelRegistryService(tmp_path / 'app', model_db_path=tmp_path / 'wrong.db')


@pytest.mark.parametrize('experiment_id', ['../../bad', 'a/b', 'a\\b', '.hidden', '', 'bad space'])
def test_model_artifact_rejects_invalid_experiment_identifier(tmp_path, experiment_id):
    with pytest.raises(ValueError, match='experiment_id'):
        model_manager.get_model_dir(experiment_id, models_dir=tmp_path / 'models')
    assert not (tmp_path / 'models').exists()


def test_http_model_resolver_is_bound_to_runtime_store(tmp_path):
    from scripts import visualization_server as app
    class Runtime:
        def __getattr__(self, name):
            return getattr(app, name)
    scoped = Runtime()
    scoped.ROOT = tmp_path
    scoped.MODELS_DIR = tmp_path / 'custom-store'
    scoped.MODEL_DB_PATH = scoped.MODELS_DIR / 'model.db'
    scoped.STATIC_DIR = tmp_path / 'static'
    scoped.SIMULATION_DB_PATH = tmp_path / 'sim.db'
    scoped.TRAINING_TASKS = {}
    import threading
    scoped.TASKS_LOCK = threading.Lock()
    scoped.TASK_RESOURCE_TRACKER = app.TASK_RESOURCE_TRACKER
    scoped.EXECUTION_REGISTRY = app.EXECUTION_REGISTRY
    context = context_from_runtime(scoped)
    model_dir = context.MODELS_DIR / 'model_123'
    model_dir.mkdir(parents=True)
    (model_dir / 'config.json').write_text('{}', encoding='utf-8')
    assert context.resolve_managed_model_dir('custom-store/model_123') == model_dir
    assert context.resolve_managed_model_file('custom-store/model_123', 'config.json') == model_dir / 'config.json'
    with pytest.raises(context.MODEL_REGISTRY.ManagedPathError):
        context.resolve_managed_model_dir('models/model_123', must_exist=False)
    # This object is invoked by the training workflow and forwards a stable
    # storage root; module-level model_manager defaults are not consulted.
    pipeline = context.run_evaluation.args[0].TrainingPipeline(
        db_path=tmp_path / 'anything.sqlite')
    assert pipeline.models_dir == context.MODELS_DIR
