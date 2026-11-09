"""Cordis 单内核装配中心（Composition Root）。

CLI（``evaluator``）与 Web（``scripts.core``）共享同一套组件定义与配置来源，
消除各自散落的 ``new`` 与配置分歧。进程内通过 :func:`get_container` 获取单例，
保证 CLI/Web 在同一进程内复用同一组组件实例（如 ``AlgorithmRunner``、
``MetricsEngine``），即 Cordis 顶层的「单内核」。

注意：CLI 与 Web 通常是不同进程入口，跨进程无法共享内存实例；此处统一的是
「装配定义」而非运行期内存单例。dataset 在 CLI（多特征有标签）与 Web（单维
SQLite）是两套不同契约，保留各自实现，由本容器统一提供。metrics 的数学早已
收敛到 ``evaluator.metrics``，本容器仅统一两个 facade 的装配入口。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from detectors.loader import extract_scores, load_detector
from evaluator.config_loader import (
    find_project_root,
    load_config,
    resolve_detector_specs,
)
from evaluator.dataset_manager import DatasetManager
from evaluator.experiment_manager import ExperimentManager
from evaluator.metric_engine import MetricEngine as CliMetricEngine
from evaluator.report_generator import ReportGenerator
from runner.local_runner import LocalRunner
from runner.runner_plugins.builtin import register_builtin_runners
from runner.runner_plugins.registry import get_runner
from scripts.core.algorithm_runner import AlgorithmRunner
from scripts.core.data_loader import DataLoader
from scripts.core.metrics_engine import MetricsEngine as WebMetricsEngine


class AppContainer:
    """装配 CLI 与 Web 共享的内核组件。

    无状态/可共享的组件以单例属性提供；需要调用方参数的组件以工厂方法提供。
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = Path(project_root) if project_root else Path(__file__).parent.parent
        # 共享单例组件（进程内复用）
        self._dataset_manager = DatasetManager()
        self._cli_metric_engine = CliMetricEngine()
        self._report_generator = ReportGenerator()
        self._experiment_manager = ExperimentManager()
        self._local_runner = get_runner("local") or LocalRunner()
        # 确保内置执行器（LocalRunner）已注册到插件体系
        register_builtin_runners()
        self._algorithm_runner = AlgorithmRunner()
        self._web_metric_engine = WebMetricsEngine()

    # ---- 配置（单一来源） ----
    def load_config(self, config_path: str | Path) -> dict[str, Any]:
        return load_config(str(config_path))

    def resolve_detector_specs(self, detectors: list[dict], project_root: Optional[Path] = None):
        root = Path(project_root) if project_root else self.project_root
        return resolve_detector_specs(detectors, root)

    def find_project_root(self, start: Path) -> Path:
        return find_project_root(Path(start))

    # ---- 共享检测器加载契约（CLI 与 Web 共用 detectors.loader） ----
    def load_detector(self, detector_spec: dict):
        return load_detector(detector_spec)

    def extract_scores(self, detector, test_data):
        return extract_scores(detector, test_data)

    # ---- CLI 组件 ----
    @property
    def dataset_manager(self) -> DatasetManager:
        return self._dataset_manager

    @property
    def cli_metric_engine(self) -> CliMetricEngine:
        return self._cli_metric_engine

    @property
    def report_generator(self) -> ReportGenerator:
        return self._report_generator

    @property
    def experiment_manager(self) -> ExperimentManager:
        return self._experiment_manager

    @property
    def local_runner(self) -> LocalRunner:
        return self._local_runner

    # ---- Web 组件 ----
    def make_data_loader(
        self, db_path: str | Path, limit: int = 0, train_ratio: float = 0.7
    ) -> DataLoader:
        return DataLoader(db_path=db_path, limit=limit, train_ratio=train_ratio)

    @property
    def algorithm_runner(self) -> AlgorithmRunner:
        return self._algorithm_runner

    @property
    def web_metric_engine(self) -> WebMetricsEngine:
        return self._web_metric_engine


_default_container: Optional[AppContainer] = None


def get_container() -> AppContainer:
    """进程级单例：CLI 与 Web 共享同一组内核组件实例。"""
    global _default_container  # pylint: disable=global-statement
    if _default_container is None:
        _default_container = AppContainer()
    return _default_container
