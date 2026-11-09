#!/usr/bin/env python
"""模型管理器 - 核心模块。

模型目录是训练/评估解耦的持久化边界：训练阶段保存可重新加载的 detector，
评估已发布模型时只加载并评分，禁止静默重新训练。
"""
from __future__ import annotations

import datetime
import json
import logging
import pickle
import re
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODELS_DIR: Path | None = None
_MODEL_LOCK = threading.RLock()


def init_models_dir(root_dir: str | Path | None = None) -> Path:
    """初始化模型目录。"""
    global _MODELS_DIR  # pylint: disable=global-statement
    if _MODELS_DIR is not None:
        return _MODELS_DIR
    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[2]
    _MODELS_DIR = root / "models"
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return _MODELS_DIR


def set_models_dir(models_dir: str | Path) -> None:
    """设置模型目录（测试与嵌入式运行可覆盖）。"""
    global _MODELS_DIR  # pylint: disable=global-statement
    _MODELS_DIR = Path(models_dir)
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)


def get_models_dir() -> Path:
    if _MODELS_DIR is None:
        return init_models_dir()
    return _MODELS_DIR


def get_model_dir(experiment_id: str, *, models_dir: str | Path | None = None) -> Path:
    """Resolve a model artifact inside an explicit store, or the legacy default.

    The training workflow passes ``models_dir`` from its application context;
    only compatibility callers continue to read the process-wide default.
    """
    name = str(experiment_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name):
        raise ValueError("invalid experiment_id for model artifact directory")
    root = Path(models_dir).resolve() if models_dir is not None else get_models_dir().resolve()
    model_dir = root / f"model_{name}"
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def _algorithm_type(algorithm: str) -> str:
    try:
        from detectors.registry import get_algorithm_type
        return get_algorithm_type(algorithm)
    except Exception:  # pylint: disable=broad-exception-caught
        # 未知第三方类型不得被误判为“不需要保存”的统计法。
        return "custom"


def _detector_config(saved_config: dict[str, Any] | None) -> dict[str, Any]:
    """从 config.json 的兼容结构中提取 detector 构造参数。"""
    if not isinstance(saved_config, dict):
        return {}
    inner = saved_config.get("config")
    if isinstance(inner, dict):
        params = inner.get("algorithm_params")
        if isinstance(params, dict):
            return dict(params)
        # 兼容早期直接把算法参数放在 config 字段的模型。
        reserved = {"algorithm_type", "train_stats"}
        if any(key not in reserved for key in inner):
            return {k: v for k, v in inner.items() if k not in reserved}
    params = saved_config.get("algorithm_params")
    return dict(params) if isinstance(params, dict) else {}


def _persist_detector(model_dir: Path, algorithm: str, algorithm_type: str, detector: Any) -> Path | None:
    if detector is None:
        logger.warning("算法 %s 没有可持久化的 detector 对象", algorithm)
        return None

    # 对需要结构重建的深度学习 detector，优先使用显式 checkpoint 协议。
    if hasattr(detector, "export_checkpoint") and callable(detector.export_checkpoint):
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(f"算法 {algorithm} 需要 PyTorch 才能保存 checkpoint") from exc
        checkpoint = detector.export_checkpoint()
        if not isinstance(checkpoint, dict):
            raise TypeError("export_checkpoint() must return a dict")
        checkpoint = dict(checkpoint)
        checkpoint.setdefault("format", "aas-detector-checkpoint-v1")
        checkpoint.setdefault("algorithm", algorithm)
        pt_path = model_dir / "model.pt"
        torch.save(checkpoint, pt_path)
        logger.info("模型 checkpoint 已保存到：%s", pt_path)
        return pt_path

    # 统计、机器学习、纯 Python/NumPy 深度学习以及第三方 detector 统一优先 pickle。
    pkl_path = model_dir / "model.pkl"
    try:
        with pkl_path.open("wb") as f:
            pickle.dump(detector, f)
    except Exception as exc:
        if pkl_path.exists():
            pkl_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"算法 {algorithm}（type={algorithm_type}）无法持久化；"
            "请确保 detector 可 pickle，或实现 export_checkpoint()/restore_checkpoint()"
        ) from exc
    logger.info("模型已保存到：%s", pkl_path)
    return pkl_path


def save_model(
    experiment_id: str,
    algorithm: str,
    dataset: str,
    table_name: str,
    metrics: dict,
    detector=None,
    config: dict | None = None,
    *,
    models_dir: str | Path | None = None,
) -> dict:
    """Save a detector to the calling application's model store."""
    with _MODEL_LOCK:
        return _save_model_impl(experiment_id, algorithm, dataset, table_name, metrics,
                                detector, config, models_dir=models_dir)


def _save_model_impl(
    experiment_id: str,
    algorithm: str,
    dataset: str,
    table_name: str,
    metrics: dict,
    detector=None,
    config: dict | None = None,
    *,
    models_dir: str | Path | None = None,
) -> dict:
    # Write into a hidden sibling, then commit the complete directory at once.
    # Previously, a failed pickle/JSON write left an apparently valid model_* folder.
    name = str(experiment_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name):
        raise ValueError("invalid experiment_id for model artifact directory")
    root = Path(models_dir).resolve() if models_dir is not None else get_models_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / f"model_{name}"
    if model_dir.exists() or model_dir.is_symlink():
        raise FileExistsError(f"模型产物已存在，拒绝覆盖：{model_dir}")
    staging = Path(tempfile.mkdtemp(prefix=f".staging-{name}-", dir=root))
    try:
        algorithm_type = _algorithm_type(algorithm)
        model_file = _persist_detector(staging, algorithm, algorithm_type, detector)
        model_config = {
            "experiment_id": experiment_id,
            "algorithm": algorithm,
            "algorithm_type": algorithm_type,
            "dataset": dataset,
            "table_name": table_name,
            "created_at": datetime.datetime.now().isoformat(),
            "config": config or {},
        }
        for file_name, payload in (("config.json", model_config), ("metrics.json", metrics or {})):
            with (staging / file_name).open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
        # fsync detector data too, whether pickle or checkpoint.
        if model_file is not None:
            # On Windows, fsync/FlushFileBuffers requires a writable OS handle.
            # The serialized bytes are complete; open r+b solely for durability.
            with model_file.open('r+b') as handle:
                os.fsync(handle.fileno())
            # Refuse to commit an artifact that cannot be loaded for scoring.
            validate_saved_model(staging, algorithm, experiment_id=name)
        _sync_directory(staging)
        if model_dir.exists() or model_dir.is_symlink():
            raise FileExistsError(f"模型产物已存在，拒绝覆盖：{model_dir}")
        # os.rename is atomic on the same filesystem; we never replace a
        # populated destination. Process-external concurrent writers need a
        # single service instance (documented deployment constraint).
        staging.rename(model_dir)
        _sync_directory(root)
        return {
            "model_dir": str(model_dir),
            "model_file_path": str(model_dir / model_file.name) if model_file else None,
            "config_file_path": str(model_dir / "config.json"),
            "metrics_file_path": str(model_dir / "metrics.json"),
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _sync_directory(directory: Path) -> None:
    """Best-effort directory metadata durability on platforms that permit it."""
    if os.name != 'posix':
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # Some mounted filesystems reject directory fsync. Data was
            # already committed; do not falsely report a failed model save.
            logger.warning('Directory fsync unsupported: %s', directory)
    finally:
        os.close(fd)



def load_model_config(model_dir: str | Path) -> dict[str, Any]:
    """Read persisted model metadata from ``config.json``.

    This is intentionally separate from :func:`load_model`: callers such as
    the published-model evaluation API need the training-time input contract
    (raw/clean data and preprocessing recipe) before choosing which table to
    score.
    """
    model_path = Path(model_dir)
    if not model_path.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_dir}")
    config_path = model_path / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"模型目录缺少 config.json：{model_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"模型配置无法读取：{config_path}") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"模型配置必须为 JSON 对象：{config_path}")
    return config

def load_model(model_dir: str | Path, algorithm: str):
    """加载已保存模型并返回可直接 ``score`` 的 detector。

    不再用 ``None`` 表示“统计模型无需加载”，因为发布模型评估的契约要求真正
    复用训练阶段参数；旧的 metadata-only 模型会得到明确错误，而不会静默重训。
    """
    with _MODEL_LOCK:
        return _load_model_impl(model_dir, algorithm)


def validate_saved_model(
    model_dir: str | Path,
    algorithm: str,
    *,
    experiment_id: str | None = None,
):
    """Validate that a model directory is a publishable AAS model.

    Publication is a state transition, so accepting an empty directory would
    create a registry row that can never be evaluated.  Require the persisted
    training metadata to agree with the publication request and ensure the
    artifact can actually be restored as a scoring detector.
    """
    model_path = Path(model_dir)
    if not model_path.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_dir}")

    config_path = model_path / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"模型目录缺少 config.json：{model_path}")
    try:
        saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"模型配置无法读取：{config_path}") from exc
    if not isinstance(saved_config, dict):
        raise RuntimeError(f"模型配置必须为 JSON 对象：{config_path}")

    saved_algorithm = str(saved_config.get("algorithm") or "").strip()
    requested_algorithm = str(algorithm or "").strip()
    # A published model must carry a self-consistent input layout contract.
    # Legacy artifacts without one stay publishable (verify returns None).
    from scripts.server.feature_schema import verify_stored_feature_schema
    verify_stored_feature_schema((saved_config.get("config") or {}).get("data_contract"))
    if saved_algorithm and saved_algorithm != requested_algorithm:
        raise ValueError(
            f"模型算法不匹配：config.json={saved_algorithm!r}, 请求={requested_algorithm!r}"
        )

    if experiment_id is not None:
        saved_experiment = str(saved_config.get("experiment_id") or "").strip()
        requested_experiment = str(experiment_id).strip()
        if saved_experiment and saved_experiment != requested_experiment:
            raise ValueError(
                f"模型实验ID不匹配：config.json={saved_experiment!r}, 请求={requested_experiment!r}"
            )

    detector = load_model(model_path, requested_algorithm)
    if not callable(getattr(detector, "score", None)):
        raise RuntimeError("模型未能恢复为可评分 detector")
    return detector


def _load_model_impl(model_dir: str | Path, algorithm: str):
    model_path = Path(model_dir)
    if not model_path.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_dir}")

    config_path = model_path / "config.json"
    saved_config: dict[str, Any] = {}
    if config_path.exists():
        try:
            saved_config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"模型配置无法读取：{config_path}") from exc

    pkl_path = model_path / "model.pkl"
    if pkl_path.exists():
        try:
            with pkl_path.open("rb") as f:
                detector = pickle.load(f)
        except Exception as exc:
            raise RuntimeError(f"模型文件无法加载：{pkl_path}") from exc
        if not hasattr(detector, "score"):
            raise RuntimeError(f"模型文件未恢复为 detector：{pkl_path}")
        logger.info("模型已从 %s 加载", pkl_path)
        return detector

    pt_path = model_path / "model.pt"
    if pt_path.exists():
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("加载深度学习模型需要 PyTorch") from exc

        try:
            try:
                checkpoint = torch.load(pt_path, map_location="cpu", weights_only=True)
            except TypeError:  # 兼容较旧 PyTorch
                checkpoint = torch.load(pt_path, map_location="cpu")
        except Exception as exc:
            raise RuntimeError(f"模型 checkpoint 无法读取：{pt_path}") from exc

        if not isinstance(checkpoint, dict) or checkpoint.get("format") != "aas-detector-checkpoint-v1":
            raise RuntimeError(
                "检测到旧式仅权重 model.pt，缺少模型结构/配置，无法保证复用评估；请重新训练并发布模型"
            )

        detector_config = _detector_config(saved_config)
        checkpoint_config = checkpoint.get("config")
        if isinstance(checkpoint_config, dict):
            detector_config = {**detector_config, **checkpoint_config}
        if str(detector_config.get("device", "cpu")).startswith("cuda") and not torch.cuda.is_available():
            detector_config["device"] = "cpu"

        try:
            from detectors.loader import load_detector
            detector = load_detector({"name": algorithm, "config": detector_config})
        except Exception as exc:
            raise RuntimeError(f"无法按算法 {algorithm} 重建 detector") from exc

        restore = getattr(detector, "restore_checkpoint", None)
        if not callable(restore):
            raise RuntimeError(
                f"算法 {algorithm} 的 checkpoint 需要 detector 实现 restore_checkpoint()"
            )
        restore(checkpoint)
        if not hasattr(detector, "score"):
            raise RuntimeError(f"算法 {algorithm} 恢复后不是可评分 detector")
        logger.info("模型 checkpoint 已从 %s 恢复", pt_path)
        return detector

    raise FileNotFoundError(
        f"模型目录缺少可加载的 model.pkl/model.pt：{model_path}；"
        "旧的 metadata-only 模型需要重新训练并发布"
    )


_MODELS_DIR = init_models_dir()
