"""清洁表管理与数据预处理模块。

从 visualization_server.py 中提取清洁表检查与预处理逻辑，
提供统一的 PreprocessingManager 接口。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from scripts.server.sqlite_guard import sqlite_connection as guarded_sqlite_connection
from scripts.server import series_catalog as catalog

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SIMULATION_DB_PATH = _PROJECT_ROOT / "datasets" / "simulation.db"
from typing import Any

from scripts.core.preproc_plugins.registry import DEFAULT_PIPELINE, run_pipeline

logger = logging.getLogger(__name__)


@dataclass
class CleanTableStatus:
    """清洁表状态。"""
    status: str  # "valid" | "invalidated" | "not_exist" | "error"
    clean_table_name: str
    message: str
    last_processed: str | None = None
    row_count: int = 0
    # 额外信息（用于 invalidated 状态）
    original_max_ts: str | None = None
    original_count: int = 0
    clean_max_ts: str | None = None
    clean_count: int = 0


class PreprocessingManager:
    """清洁表管理与数据预处理。

    Args:
        db_path: SQLite 数据库路径。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    # ─── 公共 API ───────────────────────────────────────────────

    def check_status(self, table_name: str) -> CleanTableStatus:
        """检查清洁表状态。

        依次检查：
        1. 数据库文件是否存在
        2. 原始表是否存在
        3. 清洁表是否存在
        4. 清洁表是否仍有效（与原始表快照对比）
        """
        clean_table_name = self.get_clean_table_name(table_name)

        if not self.db_path.exists():
            logger.error("数据库不存在：%s", self.db_path)
            return CleanTableStatus(
                status="error",
                clean_table_name=clean_table_name,
                message=f"数据库不存在：{self.db_path}",
            )

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            tables = self._list_tables(conn)

            if table_name not in tables:
                logger.error("原始表不存在：%s", table_name)
                return CleanTableStatus(
                    status="error",
                    clean_table_name=clean_table_name,
                    message=f"原始表不存在：{table_name}",
                )

            if clean_table_name not in tables:
                logger.info("清洁表不存在：%s", clean_table_name)
                return CleanTableStatus(
                    status="not_exist",
                    clean_table_name=clean_table_name,
                    message=f"清洁表不存在：{clean_table_name}",
                )

            # 检查有效性
            return self._check_validity(conn, table_name, clean_table_name)

    def run_preprocessing(
        self,
        table_name: str,
        pipeline_config: dict[str, Any] | None = None,
    ) -> str:
        """运行数据预处理管线，返回清洁表名。

        管线步骤：
        1. 填充缺失值（前向 + 后向填充）
        2. 修复异常值（3σ 原则）
        3. 数据平滑（移动平均）
        4. 归一化（min_max / zscore）

        Args:
            table_name: 原始表名。
            pipeline_config: 管线配置，可覆盖默认参数。
                - fill_missing: bool, 默认 True
                - repair_outlier: bool, 默认 True
                - sigma_multiplier: float, 默认 3.0
                - smooth: bool, 默认 True
                - smooth_window: int, 默认 3
                - normalize_method: str, 默认 'min_max' ('none' 跳过)
        """
        if pipeline_config is None:
            pipeline_config = {}

        clean_table_name = self.get_clean_table_name(table_name)

        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在：{self.db_path}")

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            tables = self._list_tables(conn)
            if table_name not in tables:
                raise ValueError(f"原始表不存在：{table_name}")

            conn.row_factory = sqlite3.Row
            identity = catalog.signature(conn, table_name) if catalog.is_compact(conn, table_name) else None
            fields = catalog.logical_columns(conn, table_name) if identity else []
            rows = [
                catalog.expand_known(dict(row), identity, fields) if identity else dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {catalog.quote(table_name)} ORDER BY timestamp"
                ).fetchall()
            ]

        if not rows:
            raise ValueError(f"原始表为空：{table_name}")

        # 执行预处理算子插件管线：填充缺失 → 修复异常值 → 平滑 → 归一化
        rows = run_pipeline(DEFAULT_PIPELINE, rows, pipeline_config)

        # 写入清洁表
        self._write_clean_table(table_name, clean_table_name, rows)

        logger.info("[预处理] 完成，已创建清洁表：%s", clean_table_name)

        return clean_table_name

    def get_clean_table_name(self, table_name: str) -> str:
        """获取清洁表名。"""
        return f"{table_name}_clean"

    def ensure_clean_table(
        self,
        table_name: str,
        auto_preprocess: bool = True,
        pipeline_config: dict[str, Any] | None = None,
    ) -> str:
        """确保清洁表存在，若不存在或失效则自动预处理。

        Args:
            table_name: 原始表名。
            auto_preprocess: 是否自动运行预处理。
            pipeline_config: 预处理管线配置。

        Returns:
            清洁表名。
        """
        status = self.check_status(table_name)
        clean_table_name = status.clean_table_name

        if status.status == "valid":
            logger.debug("清洁表 %s 已存在且有效", clean_table_name)
            return clean_table_name

        if not auto_preprocess:
            logger.warning(
                "清洁表 %s 状态为 %s，但 auto_preprocess=False",
                clean_table_name,
                status.status,
            )
            raise RuntimeError(
                f"清洁表不可用（状态={status.status}）：{status.message}"
            )

        logger.info(
            "清洁表 %s 状态为 %s，开始自动预处理...",
            clean_table_name,
            status.status,
        )
        return self.run_preprocessing(table_name, pipeline_config)

    # ─── 内部方法 ───────────────────────────────────────────────

    def _list_tables(self, conn: sqlite3.Connection) -> list[str]:
        """列出数据库中所有表名。"""
        return [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]

    def _check_validity(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        clean_table_name: str,
    ) -> CleanTableStatus:
        """检查清洁表相对于原始表是否有效。"""
        try:
            original_meta = conn.execute(
                f"SELECT MAX(timestamp) as max_ts, COUNT(*) as count FROM {table_name}"
            ).fetchone()
            original_max_ts = original_meta[0] if original_meta[0] else ""
            original_count = original_meta[1] if original_meta[1] else 0

            clean_meta = conn.execute(
                f"SELECT MAX(timestamp) as max_ts, COUNT(*) as count FROM {clean_table_name}"
            ).fetchone()
            clean_max_ts = clean_meta[0] if clean_meta[0] else ""
            clean_count = clean_meta[1] if clean_meta[1] else 0

            if original_max_ts != clean_max_ts or original_count != clean_count:
                logger.warning("清洁表已失效：%s", clean_table_name)
                return CleanTableStatus(
                    status="invalidated",
                    clean_table_name=clean_table_name,
                    message="清洁表已失效（源数据已变更）",
                    last_processed=clean_max_ts,
                    row_count=clean_count,
                    original_max_ts=original_max_ts,
                    original_count=original_count,
                    clean_max_ts=clean_max_ts,
                    clean_count=clean_count,
                )

            logger.debug("清洁表有效：%s", clean_table_name)
            return CleanTableStatus(
                status="valid",
                clean_table_name=clean_table_name,
                message="清洁表有效",
                last_processed=clean_max_ts,
                row_count=clean_count,
            )

        except (sqlite3.Error, TypeError, IndexError, ValueError) as e:
            logger.error("检查清洁表有效性失败：%s", e)
            return CleanTableStatus(
                status="error",
                clean_table_name=clean_table_name,
                message=f"检查失败：{e}",
            )

    def _write_clean_table(
        self,
        table_name: str,
        clean_table_name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """将处理后的数据写入清洁表。"""
        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            catalog.replace_derived_table(conn, table_name, clean_table_name, rows)

            conn.commit()
