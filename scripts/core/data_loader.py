#!/usr/bin/env python
"""
数据加载器 - 核心模块
从SQLite数据库加载数据并划分训练集/测试集，支持三种模式：
- 双表模式：train_table 和 test_table 已明确分开
- 比例划分模式：单表按 train_ratio 划分
- 简单模式：同一数据集（train = test）
"""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from scripts.server.sqlite_guard import sqlite_connection as guarded_sqlite_connection
from scripts.server import series_catalog as catalog

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SIMULATION_DB_PATH = _PROJECT_ROOT / "datasets" / "simulation.db"


def extract_binary_labels(value_rows: List[Dict[str, Any]]) -> List[int]:
    """Read ground-truth labels aligned with the samples actually being scored.

    ``DataLoader`` drops rows whose ``value`` cannot be parsed.  Reading labels
    independently from the full test table would silently shift subsequent
    labels, so callers must pass ``DataBundle.test_value_rows`` rather than
    ``DataBundle.test_rows``.

    No pseudo-label fallback is allowed for Web evaluation: every scored row
    must have an explicit binary ``label`` (0=normal, 1=fault).
    """
    labels: List[int] = []
    for index, row in enumerate(value_rows, start=1):
        if "label" not in row:
            raise ValueError(
                "评估数据校验失败：测试数据缺少 label 列；"
                "请使用包含真实故障标签（0=正常、1=故障）的测试数据表。"
            )

        raw = row["label"]
        if isinstance(raw, bool):
            labels.append(int(raw))
            continue

        if isinstance(raw, str):
            text = raw.strip().lower()
            if text in {"normal", "正常", "false", "no"}:
                labels.append(0)
                continue
            if text in {"fault", "faulty", "异常", "故障", "true", "yes"}:
                labels.append(1)
                continue

        try:
            numeric = float(raw)
        except (TypeError, ValueError, OverflowError):
            numeric = float("nan")
        if math.isfinite(numeric) and numeric in (0.0, 1.0):
            labels.append(int(numeric))
            continue

        raise ValueError(
            "评估数据校验失败：测试集中第 "
            f"{index} 条有效数值样本的 label={str(raw)[:60]!r} 无效；"
            "label 只能为 0（正常）或 1（故障），不允许为空或未知值。"
        )
    return labels


@dataclass
class DataBundle:
    """数据加载结果容器"""
    train_rows: List[Dict[str, Any]]
    test_rows: List[Dict[str, Any]]
    train_values: List[float]
    test_values: List[float]
    mode: str  # "dual_table" | "ratio_split" | "simple"
    metadata: Dict[str, Any] = field(default_factory=dict)
    train_value_rows: List[Dict[str, Any]] = field(default_factory=list)
    test_value_rows: List[Dict[str, Any]] = field(default_factory=list)
    test_labels: List[int] = field(default_factory=list)

    @property
    def train_count(self) -> int:
        """训练集样本数"""
        return len(self.train_rows)

    @property
    def test_count(self) -> int:
        """测试集样本数"""
        return len(self.test_rows)

    def summary(self) -> str:
        """生成数据摘要字符串"""
        return (
            f"DataBundle(mode={self.mode!r}, "
            f"train_rows={self.train_count}, test_rows={self.test_count}, "
            f"train_values={len(self.train_values)}, test_values={len(self.test_values)})"
        )


class DataLoader:
    """SQLite 数据加载器

    从 SQLite 数据库加载数据并根据配置划分训练集/测试集。

    Attributes:
        db_path: 数据库文件路径
        limit: 最大加载行数，0 表示不限制
        train_ratio: 训练集比例（0-1），仅在比例划分模式下使用
    """

    def __init__(
        self,
        db_path: str | Path,
        limit: int = 0,
        train_ratio: float = 0.7,
        feature_columns: Optional[List[str]] = None,
        time_column: Optional[str] = None,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
    ) -> None:
        """初始化数据加载器

        Args:
            db_path: SQLite 数据库文件路径
            limit: 最大加载行数，0 表示不限制
            train_ratio: 训练集比例（0-1），仅在比例划分模式下使用
        """
        self.db_path = Path(db_path)
        self.limit = limit
        self.train_ratio = train_ratio
        self.feature_columns = list(feature_columns or ['value'])
        # Multi-table training may populate this after loader construction.
        self.source_tables: list[str] = []
        self.logical_multi_sources: list[dict] = []
        self.logical_series: dict | None = None
        self.logical_tables: list[str] = []
        self.logical_role: str | None = None
        self.v2_channel_ids: list[int] = []
        self.v2_role: str | None = None
        self.v2_expected_sources: list[dict] | None = None
        if not self.feature_columns or len(set(self.feature_columns)) != len(self.feature_columns):
            raise ValueError('特征列不能为空或重复')
        self.time_column = time_column or None
        self.time_start = str(time_start).strip() if time_start is not None else None
        self.time_end = str(time_end).strip() if time_end is not None else None
        if (self.time_start or self.time_end) and not self.time_column:
            raise ValueError('按时间筛选必须指定时间列')
        if self.time_start and self.time_end:
            # Numeric Unix timestamps and ISO8601 timestamps require different
            # comparison rules: lexical order is not numeric order ("20" < "3").
            try:
                start_numeric = float(self.time_start)
                end_numeric = float(self.time_end)
            except (TypeError, ValueError):
                # ISO timestamps retain chronological lexical ordering.
                reversed_bounds = self.time_start > self.time_end
            else:
                reversed_bounds = start_numeric > end_numeric
            if reversed_bounds:
                raise ValueError('训练时间起点不能晚于终点')

    def load_and_split(
        self,
        train_table: Optional[str] = None,
        test_table: Optional[str] = None,
        table: Optional[str] = None,
    ) -> DataBundle:
        """加载数据并划分训练集/测试集

        根据传入参数自动选择三种模式之一：
        1. 双表模式：train_table 和 test_table 均提供
        2. 比例划分模式：只提供 table 且 train_ratio < 1.0
        3. 简单模式：只提供 table 且 train_ratio == 1.0

        Args:
            train_table: 训练集表名（双表模式）
            test_table: 测试集表名（双表模式）
            table: 单表表名（比例划分模式或简单模式）

        Returns:
            DataBundle 包含划分后的数据和数值列表

        Raises:
            ValueError: 当参数组合无效或数据提取失败时
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")

        logger.info("[DataLoader] 数据库：%s", self.db_path)

        if getattr(self,'v2_run_selection',None):
            if train_table or test_table or self.source_tables or self.logical_multi_sources or self.logical_series or self.v2_channel_ids:
                raise ValueError('V2 运行子集禁止混用旧数据源或表配置')
            return self._load_v2_run_selection()

        if self.v2_channel_ids:
            if train_table or test_table or self.source_tables or self.logical_multi_sources or self.logical_series:
                raise ValueError('V2 逻辑参量不能混用旧物理表入口')
            return self._load_v2_channels()

        if self.logical_multi_sources:
            if self.logical_series or self.source_tables or train_table or test_table:
                raise ValueError('多个逻辑参量不能与物理表联合或单参量拼接混用')
            return self._load_logical_multi()

        if self.logical_series:
            if train_table or test_table or self.source_tables:
                raise ValueError('逻辑参量时间拼接不能与双表或多参量特征对齐混用')
            return self._load_logical_series()

        # 多个单参量表经精确时间戳内连接形成临时多维输入。
        if getattr(self, 'source_tables', None):
            if train_table or test_table:
                raise ValueError('多表训练暂不支持独立训练/测试表组，请选择比例划分模式')
            return self._load_multi_source()

        # 模式判断
        if train_table and test_table:
            return self._load_dual_table(train_table, test_table)
        if table:
            if self.train_ratio < 1.0:
                return self._load_ratio_split(table)
            return self._load_simple(table)
        raise ValueError("必须提供 (train_table + test_table) 或 table 参数")

    def _load_v2_run_selection(self) -> DataBundle:
        from scripts.server.v2_run_dataset import load_selection
        sel=self.v2_run_selection
        if self.limit>0:raise ValueError('V2 运行选择禁止静默截断样本')
        rows, metadata=load_selection(self.db_path,selection=sel,role=self.v2_role,
                                     expected=self.v2_expected_sources,
                                     label_target=getattr(self, 'v2_label_target', None))
        if self.v2_role=='train':
            # Split on WHOLE RUN GROUPS, never adjacent time windows from
            # one simulation. The original held-out validation runs remain
            # untouched and are used for final published-model evaluation.
            source_ids=[x['source_id'] for x in metadata['runs']]
            if len(source_ids)==1:
                # No cross-run validation is possible. Use chronological split
                # with an embargo and never train on internal validation rows.
                # The final held-out test runs remain independent.
                n=len(rows)
                split=int(n*self.train_ratio)
                embargo=max(1,min(64,int(n*0.02)))
                train_rows=rows[:split]
                val_rows=rows[split+embargo:]
                if len(train_rows)<2 or len(val_rows)<2:
                    raise ValueError('单运行按时间划分后样本不足，无法保留验证隔离间隔')
                bundle=self._build_bundle(train_rows,val_rows,'v2_single_run_temporal_embargo')
                metadata['internal_train_source_ids']=source_ids[:]
                metadata['internal_validation_source_ids']=source_ids[:]
                metadata['internal_validation_type']='temporal_holdout_not_independent_run'
                metadata['embargo_samples']=embargo
            else:
                count=max(1,min(len(source_ids)-1,int(len(source_ids)*self.train_ratio)))
                train_ids=set(source_ids[:count]);val_ids=set(source_ids[count:])
                train_rows=[r for r in rows if r['run_source_id'] in train_ids]
                val_rows=[r for r in rows if r['run_source_id'] in val_ids]
                if len(train_rows)<2 or not val_rows:raise ValueError('按运行组划分后没有足够的拟合/验证样本')
                bundle=self._build_bundle(train_rows,val_rows,'v2_run_group_holdout')
                metadata['internal_train_source_ids']=sorted(train_ids)
                metadata['internal_validation_source_ids']=sorted(val_ids)
                metadata['internal_validation_type']='independent_run_groups' 
        else:
            bundle=self._build_bundle(rows,rows,'v2_run_heldout_scoring')
        bundle.metadata['v2_run_manifest']=metadata
        return bundle

    def _load_v2_channels(self) -> DataBundle:
        from scripts.server.telemetry_v2_training import load_matrix
        contract = getattr(self, 'v2_alignment_contract', {})
        rows, metadata = load_matrix(self.db_path, channel_ids=self.v2_channel_ids,
            role=self.v2_role, method=contract.get('method','exact'),
            tolerance_seconds=contract.get('tolerance_seconds',0),
            start=self.time_start,end=self.time_end,
            expected=self.v2_expected_sources)
        if self.limit > 0:
            raise ValueError('V2 禁止隐式限制样本数，请使用时间窗口筛选')
        if self.v2_role == 'train':
            split=int(len(rows)*self.train_ratio)
            if split<2 or len(rows)-split<1:
                raise ValueError('V2 按时间划分训练/验证集后样本不足')
            result=self._build_bundle(rows[:split],rows[split:],'v2_train_time_split')
        else:
            result=self._build_bundle(rows,rows,'v2_test_scoring')
        result.metadata['v2_alignment'] = metadata
        return result

    def _load_logical_series(self) -> DataBundle:
        """Same parameter across partitions, in globally ordered time order.

        Never borrows opposite-role partitions. Label validity is checked by
        the same normal/ground-truth validation as a physical-table run.
        """
        from scripts.server.continuous_series import load_logical_training_rows
        from scripts.server.services import sqlite_tables
        from scripts.server.logical_selection import select_role_tables, normalize_identity

        identity = normalize_identity(self.logical_series)
        role = self.logical_role
        if role not in ('train', 'test'):
            raise ValueError('逻辑参量必须指定训练或测试角色')
        current = select_role_tables(sqlite_tables(self.db_path), identity, role)
        if sorted(current) != sorted(self.logical_tables):
            raise ValueError('逻辑参量的数据用途或来源表变化，请重新选择数据')
        if self.time_column not in (None, 'timestamp'):
            raise ValueError('逻辑参量只允许 timestamp 时间列')
        if self.feature_columns != ['value']:
            raise ValueError('单参量逻辑拼接只支持 value 特征')
        rows, manifest = load_logical_training_rows(self.db_path, identity,
            start_time=self.time_start, end_time=self.time_end,
            simulation_db_path=_SIMULATION_DB_PATH, expected_tables=self.logical_tables)
        if not rows:
            raise ValueError('指定时间段内没有可用的逻辑参量数据')
        if self.limit > 0:
            rows = rows[:self.limit]
        if role == 'train' and any(extract_binary_labels([row])[0] != 0 for row in rows):
            raise ValueError('逻辑训练数据中出现故障标签，禁止作为正常训练输入')
        if self.train_ratio >= 1:
            bundle = self._build_bundle(rows, rows, 'logical_series_simple')
        else:
            split = int(len(rows) * self.train_ratio)
            if split < 2 or len(rows) - split < 1:
                raise ValueError('逻辑参量样本不足以按时间划分训练和验证集')
            bundle = self._build_bundle(rows[:split], rows[split:], 'logical_series_ratio_split')
        bundle.metadata['logical_series_manifest'] = manifest
        return bundle

    def _load_logical_multi(self) -> DataBundle:
        from scripts.core.logical_multi_fusion import fuse_logical_sources
        if self.time_column not in (None, 'timestamp'):
            raise ValueError('逻辑参量的时间列必须为 timestamp')
        if self.logical_role not in ('train', 'test'):
            raise ValueError('必须指定逻辑参量的 train/test 角色')
        rows, alignment = fuse_logical_sources(
            self.db_path, self.logical_multi_sources, role=self.logical_role,
            simulation_db_path=_SIMULATION_DB_PATH, time_start=self.time_start,
            time_end=self.time_end,
            tolerance_seconds=getattr(self, 'alignment_tolerance_seconds', 0),
            alignment_method=getattr(self, 'alignment_method', 'nearest'),
            timeline_mode=getattr(self, 'timeline_mode', 'anchor'),
            grid_interval_seconds=getattr(self, 'grid_interval_seconds', 0),
            label_alignment_method=getattr(self, 'label_alignment_method', 'matched'))
        if self.limit > 0:
            raise ValueError('逻辑多参量训练不允许隐式 limit 截断，必须明确缩小时间窗')
        if self.logical_role == 'train' and any(extract_binary_labels([row])[0] for row in rows):
            raise ValueError('联合训练数据存在故障标签，禁止用于正常训练')
        self.feature_columns = list(alignment['feature_columns'])
        if self.train_ratio >= 1:
            bundle = self._build_bundle(rows, rows, 'logical_multi_simple')
        else:
            split = int(len(rows) * self.train_ratio)
            if split < 2 or len(rows) - split < 1:
                raise ValueError('跨表对齐样本不足以按时间划分训练验证集')
            bundle = self._build_bundle(rows[:split], rows[split:], 'logical_multi_ratio_split')
        bundle.metadata['alignment'] = alignment
        return bundle

    def _load_multi_source(self) -> DataBundle:
        """Time-align source tables and split the fused vectors chronologically."""
        from scripts.core.multi_table_fusion import fuse_sources

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            rows, alignment = fuse_sources(
                conn, list(self.source_tables),
                time_start=self.time_start, time_end=self.time_end,
                tolerance_seconds=getattr(self, "alignment_tolerance_seconds", 0),
                alignment_method=getattr(self, "alignment_method", 'nearest'),
                timeline_mode=getattr(self, "timeline_mode", 'anchor'),
                grid_interval_seconds=getattr(self, "grid_interval_seconds", 0),
                label_alignment_method=getattr(self, "label_alignment_method", 'matched'),
            )
        if self.limit > 0:
            rows = rows[:self.limit]
        self.feature_columns = alignment['feature_columns']
        split = int(len(rows) * self.train_ratio)
        if self.train_ratio >= 1.0:
            bundle = self._build_bundle(rows, rows, 'multi_table_simple')
        else:
            if split < 2 or len(rows) - split < 1:
                raise ValueError('对齐后样本量不足以按当前比例划分训练/验证集')
            bundle = self._build_bundle(rows[:split], rows[split:], 'multi_table_ratio_split')
        bundle.metadata['alignment'] = alignment
        return bundle

    def _load_dual_table(self, train_table: str, test_table: str) -> DataBundle:
        """双表模式：分别加载训练集和测试集"""
        logger.info("[DataLoader] 训练集表：%s", train_table)
        logger.info("[DataLoader] 测试集表：%s", test_table)

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            train_rows = self._fetch_rows(conn, train_table)
            test_rows = self._fetch_rows(conn, test_table)

        logger.info("[DataLoader] 训练集样本数：%d", len(train_rows))
        logger.info("[DataLoader] 测试集样本数：%d", len(test_rows))

        return self._build_bundle(train_rows, test_rows, mode="dual_table")

    def _load_ratio_split(self, table: str) -> DataBundle:
        """比例划分模式：单表按比例划分"""
        logger.info("[DataLoader] 数据表：%s", table)
        logger.info("[DataLoader] 训练集比例：%.0f%%", self.train_ratio * 100)

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            all_rows = self._fetch_rows(conn, table, filter_time=True)

        split_idx = int(len(all_rows) * self.train_ratio)
        train_rows = all_rows[:split_idx]
        test_rows = all_rows[split_idx:]

        logger.info("[DataLoader] 划分后训练集样本数：%d", len(train_rows))
        logger.info("[DataLoader] 划分后测试集样本数：%d", len(test_rows))

        return self._build_bundle(train_rows, test_rows, mode="ratio_split")

    def _load_simple(self, table: str) -> DataBundle:
        """简单模式：同一数据集（train = test）"""
        logger.info("[DataLoader] 数据表：%s（简单模式）", table)

        with guarded_sqlite_connection(self.db_path, simulation_db_path=_SIMULATION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            train_rows = self._fetch_rows(conn, table, filter_time=True)

        test_rows = train_rows
        logger.info("[DataLoader] 样本数：%d（简单模式：训练集=测试集）", len(train_rows))

        return self._build_bundle(train_rows, test_rows, mode="simple")

    def _fetch_rows(self, conn: sqlite3.Connection, table_name: str, *, filter_time: bool = False) -> List[Dict[str, Any]]:
        """从指定表获取数据行

        Args:
            conn: SQLite 连接对象
            table_name: 表名

        Returns:
            字典列表，每行一个字典
        """
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if table_name not in tables:
            raise ValueError(f"数据表不存在：{table_name}")
        quoted = '"' + str(table_name).replace('"', '""') + '"'
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info({quoted})')}
        missing = [col for col in self.feature_columns if col not in columns]
        if missing:
            raise ValueError(f'数据表 {table_name} 缺少所选特征：{missing}')
        query = f"SELECT * FROM {quoted}"
        params: list[Any] = []
        if filter_time and self.time_column:
            if self.time_column not in columns:
                raise ValueError(f'时间列不存在：{self.time_column}')
            qtime = '"' + self.time_column.replace('"','""') + '"'
            conditions = []
            if self.time_start:
                conditions.append(f'{qtime} >= ?')
                params.append(self.time_start)
            if self.time_end:
                conditions.append(f'{qtime} <= ?')
                params.append(self.time_end)
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
            query += f' ORDER BY {qtime} ASC, rowid ASC'
        if self.limit > 0:
            query += ' LIMIT ?'
            params.append(self.limit)
        identity = catalog.signature(conn, table_name) if catalog.is_compact(conn, table_name) else None
        fields = catalog.logical_columns(conn, table_name) if identity else []
        rows = [catalog.expand_known(dict(row), identity, fields) if identity else dict(row)
                for row in conn.execute(query, params).fetchall()]
        if getattr(self, 'label_alignment_method', 'matched') == 'interval':
            from scripts.core.fault_intervals import load_lookup
            from scripts.core.multi_table_fusion import _moment
            lookup = load_lookup(conn, table_name)
            if lookup.kind is None:
                raise ValueError(f'{table_name} 缺少故障/已确认正常区间标注')
            annotated = []
            for row in rows:
                truth = lookup.at(_moment(row.get('timestamp')))
                if truth is not None:
                    annotated.append({**row, 'label': truth})
            if not annotated:
                raise ValueError(f'{table_name} 没有任何已标注的有效样本')
            return annotated
        return rows

    def _extract_values(self, rows: List[Dict[str, Any]]) -> List[float]:
        """从行数据中提取 value 列的数值

        Args:
            rows: 行数据字典列表

        Returns:
            浮点数列表

        Raises:
            ValueError: 当没有找到有效数值时
        """
        values = []
        for row in rows:
            if "value" in row and row["value"] is not None:
                try:
                    values.append(float(row["value"]))
                except (TypeError, ValueError) as e:
                    logger.warning("[DataLoader] 跳过无效值：%s，错误：%s", row["value"], e)

        return values

    def _extract_value_rows(
        self, rows: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[float]]:
        """Return rows with valid numeric ``value`` entries and aligned floats."""
        valid_rows: List[Dict[str, Any]] = []
        values: List[float] = []
        for row in rows:
            try:
                vector = [float(row[col]) for col in self.feature_columns]
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in vector):
                continue
            valid_rows.append(row)
            values.append(vector[0] if len(vector) == 1 else vector)
        return valid_rows, values

    def _build_bundle(
        self,
        train_rows: List[Dict[str, Any]],
        test_rows: List[Dict[str, Any]],
        mode: str,
    ) -> DataBundle:
        """构建 DataBundle 对象

        Args:
            train_rows: 训练集行数据
            test_rows: 测试集行数据
            mode: 数据加载模式

        Returns:
            DataBundle 实例

        Raises:
            ValueError: 当训练集或测试集没有有效数值时
        """
        train_value_rows, train_values = self._extract_value_rows(train_rows)
        test_value_rows, test_values = self._extract_value_rows(test_rows)

        if not train_values:
            raise ValueError("训练集中没有有效的数值数据")
        if not test_values:
            raise ValueError("测试集中没有有效的数值数据")

        bundle = DataBundle(
            train_rows=train_rows,
            test_rows=test_rows,
            train_values=train_values,
            test_values=test_values,
            mode=mode,
            metadata={
                "db_path": str(self.db_path),
                "train_count": len(train_rows),
                "test_count": len(test_rows),
                "train_valid_count": len(train_value_rows),
                "test_valid_count": len(test_value_rows),
                "limit": self.limit,
                "feature_columns": list(self.feature_columns),
                "time_column": self.time_column,
                "time_start": self.time_start,
                "time_end": self.time_end,
            },
            train_value_rows=train_value_rows,
            test_value_rows=test_value_rows,
        )

        logger.info("[DataLoader] %s", bundle.summary())
        return bundle
