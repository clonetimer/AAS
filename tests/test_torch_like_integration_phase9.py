"""Five adapted torch_like algorithms through AAS V2 registration and lifecycle."""
from __future__ import annotations

import numpy as np
import pytest

from detectors.loader import load_detector
from detectors.registry import get_detector_config_schema, get_available_algorithms
from scripts.core.algorithm_runner import AlgorithmRunner
from scripts.server.v2_algorithm_parameters import validate_algorithm_configuration


NAMES = (
    "torch_like_cusum", "torch_like_ewma", "torch_like_pca_spe",
    "torch_like_usad", "torch_like_tranad",
)


def _params(name):
    if name == "torch_like_usad":
        return {"epochs": 1, "batch_size": 16, "hidden_dim": 12, "latent_dim": 4,
                "window_size": 4, "random_state": 23}
    if name == "torch_like_tranad":
        return {"epochs": 1, "batch_size": 16, "d_model": 8, "n_heads": 2,
                "d_ff": 16, "window_size": 4, "random_state": 23}
    return {}


@pytest.fixture(scope="module")
def arrays():
    rng = np.random.default_rng(12)
    healthy = rng.normal(size=(32, 3)).astype(np.float32)
    testing = rng.normal(size=(16, 3)).astype(np.float32)
    testing[12:] += 3.0
    return healthy, testing


@pytest.mark.parametrize("name", NAMES)
def test_registration_and_schema(name):
    options = {entry["name"] for entry in get_available_algorithms()}
    assert name in options
    schema = get_detector_config_schema(name)
    assert schema["decision_modes"] == ["score_threshold"]
    assert validate_algorithm_configuration(name, _params(name)) == _params(name)
    with pytest.raises(ValueError, match="未知模型参数"):
        validate_algorithm_configuration(name, {"unregistered_parameter": 1})


@pytest.mark.parametrize("name", NAMES)
def test_run_save_reload_and_score(name, arrays, tmp_path):
    train, test = arrays
    runner = AlgorithmRunner()
    params = _params(name)
    result = runner.run(name, train.tolist(), test.tolist(), params,
                        train_segments=[16, 16], test_segments=[8, 8])
    assert len(result.raw_scores) == len(test)
    assert len(result.pred_labels) == len(test)
    assert all(np.isfinite(result.raw_scores))
    assert result.detector is not None

    from scripts.core import model_manager
    old = model_manager.get_models_dir()
    try:
        model_manager.set_models_dir(tmp_path)
        model_manager.save_model(
            "torch_like_test", name, "v2", "logical_channels", {},
            detector=result.detector,
            config={"config": {"algorithm_params": params}},
        )
        path = tmp_path / "model_torch_like_test"
        restored = model_manager.load_model(str(path), name)
        repeated = runner.run_preloaded(name, test.tolist(), restored,
                                        test_segments=[8, 8])
        assert np.allclose(repeated.raw_scores, result.raw_scores, rtol=1e-5, atol=1e-5)
        assert repeated.pred_labels == result.pred_labels
    finally:
        model_manager.set_models_dir(old)


def test_cusum_and_ewma_restart_at_run_boundary():
    healthy = np.array([[-1.], [1.], [-1.], [1.]] * 8)
    x = np.ones((8, 1)) * 3.0
    for name in ("torch_like_cusum", "torch_like_ewma"):
        model = load_detector({"name": name, "config": {}})
        model.fit(healthy)
        model.set_segments([4, 4])
        a = model.score(x)
        model.set_segments([4])
        b = model.score(x[:4])
        assert a[:4] == pytest.approx(b)
        assert a[4:] == pytest.approx(b)  # no past state from first run


def test_neural_causal_windows_restart_at_run_boundary():
    model=load_detector({"name":"torch_like_usad", "config":{"window_size":3,"epochs":1}})
    x=np.array([[1.], [2.], [3.], [99.], [100.]],dtype=np.float32)
    model.set_segments([3,2])
    windows=model._prepare(x)
    assert windows.shape==(5,3,1)
    assert windows[2,:,0].tolist()==[1.,2.,3.]
    assert windows[3,:,0].tolist()==[99.,99.,99.]
    assert windows[4,:,0].tolist()==[99.,99.,100.]


def test_pca_requires_multiple_channels(arrays):
    train, _ = arrays
    detector=load_detector({"name":"torch_like_pca_spe","config":{}})
    with pytest.raises(ValueError,match="PCA-SPE needs"):
        detector.fit(train[:,:1])


def test_hyperparameter_validation():
    with pytest.raises(ValueError):
        validate_algorithm_configuration("torch_like_tranad",{"n_heads":0})
    with pytest.raises(ValueError):
        load_detector({"name":"torch_like_tranad","config":{"d_model":15,"n_heads":4}})


def test_pca_and_tranad_preflight_constraints():
    # Pure hyperparameter checks happen in the constructed detector; the V2
    # training endpoint additionally validates selected feature count.
    with pytest.raises(ValueError, match="PCA-SPE needs"):
        detector = load_detector({"name":"torch_like_pca_spe", "config":{"n_components":2}})
        detector.fit(np.ones((8,2)))
