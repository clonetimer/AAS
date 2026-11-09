from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from evaluator.dataset_manager import DatasetManager
from detectors.loader import load_detector, detect_with_detector
from runner.result_schema import DetectorOutput, failed_result, success_result

# 检测器依赖映射
DETECTOR_DEPENDENCIES = {
    "MLPDetector": ["torch", "torchvision"],
    "LSTMAutoEncoderDetector": ["torch", "torchvision"],
    "IsolationForestDetector": ["scikit-learn"],
    "DummyDetector": [],
}

def _extract_missing_packages(error_msg: str) -> list[str]:
    """从 ImportError 消息中提取缺失的包名"""
    import re

    # 匹配 "No module named 'xxx'" 格式
    matches = re.findall(r"No module named '([^']+)'", error_msg)
    if matches:
        return matches

    # 匹配 "cannot import name" 格式（可能是子模块）
    submodule_matches = re.findall(r"cannot import name '[^']+' from '([^']+)'", error_msg)
    if submodule_matches:
        return submodule_matches

    return []

def _generate_install_command(packages: list[str]) -> str:
    """根据缺失包生成安装命令"""
    # 包名映射：有些包名和模块名不同
    package_name_map = {
        "sklearn": "scikit-learn",
        "torchvision": "torchvision",
        "torch": "torch",
    }

    pip_packages = []
    for pkg in packages:
        pip_packages.append(package_name_map.get(pkg, pkg))

    return f"pip install {' '.join(pip_packages)}"

def _get_detector_dependencies(detector_name: str) -> list[str]:
    """获取检测器所需的依赖包"""
    return DETECTOR_DEPENDENCIES.get(detector_name, [])

def _check_detector_dependencies(detector_name: str) -> tuple[bool, list[str]]:
    """检查检测器依赖是否已安装"""
    missing = []
    for dep in _get_detector_dependencies(detector_name):
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    return (len(missing) == 0, missing)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runner subprocess entrypoint.")
    parser.add_argument("--task", required=True, help="Task json path.")
    return parser.parse_args()


def load_task_json(task_path: str) -> dict:
    return json.loads(Path(task_path).read_text(encoding="utf-8"))


def save_result_json(result: dict, result_file: str) -> None:
    Path(result_file).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def execute_task(task: dict) -> dict:
    detector_spec = task["detector"]
    dataset_payload = json.loads(Path(task["dataset"]["dataset_file"]).read_text(encoding="utf-8"))
    if "train_X" not in dataset_payload:
        meta = dataset_payload.get("meta")
        if not isinstance(meta, dict):
            return failed_result("DATA_LOAD_ERROR", "dataset snapshot missing train_X and meta")
        try:
            bundle = DatasetManager().load_dataset_from_meta(meta)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return failed_result("DATA_LOAD_ERROR", f"{type(exc).__name__}: {exc}")
        dataset_payload = {
            "train_X": bundle.train_X,
            "train_y": bundle.train_y,
            "valid_X": bundle.valid_X,
            "valid_y": bundle.valid_y,
            "test_X": bundle.test_X,
            "test_y": bundle.test_y,
            "feature_names": bundle.feature_names,
            "meta": bundle.meta,
        }

    try:
        detector = load_detector(detector_spec)
    except ImportError as exc:
        missing_packages = _extract_missing_packages(str(exc))
        install_command = _generate_install_command(missing_packages)
        error_msg = f"依赖包未安装: {', '.join(missing_packages)}\n安装命令: {install_command}"
        return failed_result("PLUGIN_LOAD_ERROR", error_msg)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return failed_result("PLUGIN_LOAD_ERROR", f"{type(exc).__name__}: {exc}")

    runtime = {
        "train_time_ms": 0,
        "score_time_ms": 0,
        "total_time_ms": 0,
        "peak_memory_mb": None,
        "timeout": False,
    }

    t0 = time.perf_counter()
    try:
        fit_start = time.perf_counter()
        detector.fit(
            dataset_payload["train_X"],
            dataset_payload.get("train_y"),
            dataset_payload.get("valid_X"),
            dataset_payload.get("valid_y"),
        )
        needs_calibration = getattr(detector, "needs_threshold_calibration", None)
        if callable(needs_calibration) and needs_calibration():
            from detectors.loader import extract_scores
            reference_X = dataset_payload.get("valid_X")
            if reference_X is None or len(reference_X) == 0:
                reference_X = dataset_payload["train_X"]
            detector.fit_threshold(extract_scores(detector, reference_X))
        fit_end = time.perf_counter()
        runtime["train_time_ms"] = int((fit_end - fit_start) * 1000)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        runtime["total_time_ms"] = int((time.perf_counter() - t0) * 1000)
        return failed_result("FIT_RUNTIME_ERROR", f"{type(exc).__name__}: {exc}", detector.get_meta(), runtime)

    pred_label = None
    threshold = None
    threshold_trace = None
    decision_mode = getattr(detector, "decision_mode", None)
    scores = None
    try:
        score_start = time.perf_counter()
        if task["runtime"].get("predict_label", True):
            detection = detect_with_detector(
                detector, dataset_payload["test_X"], preserve_state=True
            )
            scores = detection.get("anomaly_score")
            pred_label = detection.get("pred_label")
            threshold = detection.get("threshold")
            threshold_trace = detection.get("thresholds")
            decision_mode = detection.get("decision_mode") or decision_mode
        else:
            from detectors.loader import extract_scores
            scores = extract_scores(detector, dataset_payload["test_X"])
        score_end = time.perf_counter()
        runtime["score_time_ms"] = int((score_end - score_start) * 1000)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        runtime["total_time_ms"] = int((time.perf_counter() - t0) * 1000)
        code = "PREDICT_RUNTIME_ERROR" if task["runtime"].get("predict_label", True) else "SCORE_RUNTIME_ERROR"
        return failed_result(code, f"{type(exc).__name__}: {exc}", detector.get_meta(), runtime)

    runtime["total_time_ms"] = int((time.perf_counter() - t0) * 1000)
    return success_result(
        detector_meta=detector.get_meta(),
        runtime=runtime,
        output=DetectorOutput(
            anomaly_score=list(scores) if scores is not None else None,
            pred_label=list(pred_label) if pred_label is not None else None,
            threshold=threshold,
            thresholds=threshold_trace,
            decision_mode=decision_mode,
            extra={},
        ),
    )


def main() -> int:
    args = parse_args()
    task = load_task_json(args.task)
    result = execute_task(task)
    save_result_json(result, task["output"]["result_file"])
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
