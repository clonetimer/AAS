from __future__ import annotations

import math
import random
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluator.datasource_plugins.registry import DATASOURCE_REGISTRY, get_datasource


@dataclass
class DatasetBundle:
    train_X: list[list[float]]
    train_y: list[int] | None
    valid_X: list[list[float]] | None
    valid_y: list[int] | None
    test_X: list[list[float]]
    test_y: list[int] | None
    feature_names: list[str]
    timestamp: list[str] | None
    meta: dict[str, Any]


class DatasetManager:
    def load_dataset(self, dataset_config: dict) -> DatasetBundle:
        file_path = Path(dataset_config["file"])
        data_format = str(dataset_config.get("format", "csv")).lower()
        mode = str(dataset_config.get("mode", "basic")).lower()

        source = get_datasource(data_format)
        if source is None:
            raise ValueError(
                f"Unsupported dataset format: {data_format!r}. "
                f"Available: {sorted(DATASOURCE_REGISTRY)}"
            )

        if data_format == "csv":
            rows = source.read_rows({"file": file_path}).rows
            self.validate_schema(rows, dataset_config)
        elif data_format in {"sqlite", "db", "historydb"}:
            table_name = str(dataset_config.get("table", "history_data"))
            if mode == "history_db":
                clean_table = str(dataset_config.get("clean_table", f"{table_name}_clean"))
                if source.table_exists({"file": file_path, "table": clean_table}):
                    rows = source.read_rows({"file": file_path, "table": clean_table}).rows
                    self.validate_schema(rows, dataset_config)
                    return self._finalize_bundle_meta(self.build_history_bundle(
                        rows,
                        dataset_config,
                        data_source_table=clean_table,
                        used_clean_table=True,
                    ), dataset_config)
                rows = source.read_rows({"file": file_path, "table": table_name}).rows
                self.validate_schema(rows, dataset_config)
                return self._finalize_bundle_meta(self.build_history_bundle(
                    rows,
                    dataset_config,
                    source_db_file=file_path,
                    target_clean_table=clean_table,
                    data_source_table=table_name,
                    used_clean_table=False,
                ), dataset_config)
            rows = source.read_rows({"file": file_path, "table": table_name}).rows
            self.validate_schema(rows, dataset_config)
        else:
            raise ValueError("Only CSV and SQLite(historydb) datasets are supported.")

        if mode == "history_db":
            return self._finalize_bundle_meta(self.build_history_bundle(rows, dataset_config), dataset_config)
        if mode == "simulated_db":
            return self._finalize_bundle_meta(self.build_simulated_bundle(rows, dataset_config), dataset_config)
        return self._finalize_bundle_meta(self.split_dataset(rows, dataset_config), dataset_config)

    def load_dataset_from_meta(self, meta: dict[str, Any]) -> DatasetBundle:
        dataset_config = meta.get("dataset_config")
        if not isinstance(dataset_config, dict):
            raise ValueError("dataset meta missing dataset_config, cannot reconstruct dataset.")
        return self.load_dataset(dataset_config)

    def _finalize_bundle_meta(self, bundle: DatasetBundle, dataset_config: dict[str, Any]) -> DatasetBundle:
        bundle.meta = {
            **bundle.meta,
            "dataset_config": {
                "file": dataset_config.get("file"),
                "format": dataset_config.get("format", "csv"),
                "mode": dataset_config.get("mode", "basic"),
                "table": dataset_config.get("table"),
                "clean_table": dataset_config.get("clean_table"),
                "feature_cols": dataset_config.get("feature_cols", []),
                "label_col": dataset_config.get("label_col"),
                "timestamp_col": dataset_config.get("timestamp_col"),
                "split": dataset_config.get("split", {}),
                "preprocess": dataset_config.get("preprocess", {}),
                "simulation": dataset_config.get("simulation", {}),
            },
            "snapshot_version": 2,
        }
        return bundle

    def validate_schema(self, rows: list[dict[str, str]], schema_config: dict) -> None:
        if not rows:
            raise ValueError("Dataset is empty.")

        required_cols = list(schema_config.get("feature_cols", []))
        label_col = schema_config.get("label_col")
        timestamp_col = schema_config.get("timestamp_col")
        if label_col:
            required_cols.append(label_col)
        if timestamp_col:
            required_cols.append(timestamp_col)

        missing = [column for column in required_cols if column not in rows[0]]
        if missing:
            raise ValueError(f"Dataset missing required columns: {missing}")

    def split_dataset(self, rows: list[dict[str, str]], split_config: dict) -> DatasetBundle:
        feature_cols = split_config["feature_cols"]
        label_col = split_config.get("label_col")
        timestamp_col = split_config.get("timestamp_col")
        split = split_config.get("split", {})

        train_ratio = float(split.get("train_ratio", 0.6))
        valid_ratio = float(split.get("valid_ratio", 0.2))
        test_ratio = float(split.get("test_ratio", 0.2))
        ratio_sum = train_ratio + valid_ratio + test_ratio
        if abs(ratio_sum - 1.0) > 1e-9:
            raise ValueError("train_ratio + valid_ratio + test_ratio must equal 1.0")

        total = len(rows)
        train_end = int(total * train_ratio)
        valid_end = train_end + int(total * valid_ratio)

        train_rows = rows[:train_end]
        valid_rows = rows[train_end:valid_end]
        test_rows = rows[valid_end:]

        train_X, train_y = self.extract_features_and_labels(train_rows, feature_cols, label_col)
        valid_X, valid_y = self.extract_features_and_labels(valid_rows, feature_cols, label_col)
        test_X, test_y = self.extract_features_and_labels(test_rows, feature_cols, label_col)

        timestamp = [row[timestamp_col] for row in rows] if timestamp_col else None
        return DatasetBundle(
            train_X=train_X,
            train_y=train_y,
            valid_X=valid_X if valid_rows else None,
            valid_y=valid_y if valid_rows else None,
            test_X=test_X,
            test_y=test_y if test_rows else None,
            feature_names=feature_cols,
            timestamp=timestamp,
            meta={"source_file": str(Path(split_config["file"]).resolve()), "total_rows": total},
        )

    def build_history_bundle(
        self,
        rows: list[dict[str, str]],
        config: dict,
        source_db_file: Path | None = None,
        target_clean_table: str | None = None,
        data_source_table: str | None = None,
        used_clean_table: bool = False,
    ) -> DatasetBundle:
        feature_cols = config["feature_cols"]
        label_col = config.get("label_col")
        if not label_col:
            raise ValueError("history_db mode requires label_col for normal/fault split.")

        preprocessed_rows = self.preprocess_rows(rows, feature_cols, config.get("preprocess", {}))
        clean_table_created = False
        if source_db_file and target_clean_table:
            self._write_sqlite(source_db_file, target_clean_table, preprocessed_rows)
            clean_table_created = True

        split = config.get("split", {})
        train_normal_ratio = float(split.get("train_normal_ratio", 0.8))
        if not (0.0 < train_normal_ratio < 1.0):
            raise ValueError("train_normal_ratio must be in (0, 1).")
        seed = int(split.get("seed", 42))

        normal_rows = [row for row in preprocessed_rows if int(float(row[label_col])) == 0]
        fault_rows = [row for row in preprocessed_rows if int(float(row[label_col])) == 1]
        if not normal_rows:
            raise ValueError("history_db mode requires at least one normal sample (label=0).")

        rng = random.Random(seed)
        shuffled = list(normal_rows)
        rng.shuffle(shuffled)

        train_end = int(len(shuffled) * train_normal_ratio)
        train_rows = shuffled[:train_end]
        test_rows = shuffled[train_end:] + fault_rows

        train_X, train_y = self.extract_features_and_labels(train_rows, feature_cols, label_col)
        test_X, test_y = self.extract_features_and_labels(test_rows, feature_cols, label_col)

        timestamp_col = config.get("timestamp_col")
        timestamp = [row[timestamp_col] for row in preprocessed_rows] if timestamp_col else None
        return DatasetBundle(
            train_X=train_X,
            train_y=train_y,
            valid_X=None,
            valid_y=None,
            test_X=test_X,
            test_y=test_y,
            feature_names=feature_cols,
            timestamp=timestamp,
            meta={
                "source_file": str(Path(config["file"]).resolve()),
                "total_rows": len(preprocessed_rows),
                "mode": "history_db",
                "normal_rows": len(normal_rows),
                "fault_rows": len(fault_rows),
                "train_normal_ratio": train_normal_ratio,
                "data_source_table": data_source_table,
                "clean_table": target_clean_table or data_source_table,
                "used_clean_table": used_clean_table,
                "clean_table_created": clean_table_created,
            },
        )

    def build_simulated_bundle(self, rows: list[dict[str, str]], config: dict) -> DatasetBundle:
        feature_cols = config["feature_cols"]
        label_col = config.get("label_col", "label")
        simulation = config.get("simulation", {})
        method = str(simulation.get("method", "digital")).lower()

        base_rows = self.preprocess_rows(rows, feature_cols, config.get("preprocess", {}))
        normal_rows = [row for row in base_rows if int(float(row.get(label_col, "0"))) == 0]
        if not normal_rows:
            raise ValueError("simulated_db mode requires normal samples as base data.")

        if method == "propagation":
            generated_fault_rows = self.inject_propagation_faults(normal_rows, feature_cols, simulation, label_col)
        else:
            generated_fault_rows = self.inject_digital_faults(normal_rows, feature_cols, simulation, label_col)

        all_rows = normal_rows + generated_fault_rows
        split = dict(config.get("split", {}))
        split.setdefault("train_ratio", 0.8)
        split.setdefault("valid_ratio", 0.0)
        split.setdefault("test_ratio", 0.2)

        simulated_config = {
            **config,
            "label_col": label_col,
            "split": split,
        }
        bundle = self.split_dataset(all_rows, simulated_config)
        bundle.meta.update(
            {
                "mode": "simulated_db",
                "simulation_method": method,
                "base_rows": len(normal_rows),
                "generated_fault_rows": len(generated_fault_rows),
            }
        )
        return bundle

    def preprocess_rows(
        self,
        rows: list[dict[str, str]],
        feature_cols: list[str],
        preprocess: dict[str, Any],
    ) -> list[dict[str, str]]:
        if not rows or not preprocess:
            return [dict(row) for row in rows]

        out = [dict(row) for row in rows]

        for col in feature_cols:
            values = [self._to_finite_float(row.get(col)) for row in out]

            if preprocess.get("fill_missing", True):
                values = self._fill_missing_with_neighbor_mean(values)

            if preprocess.get("repair_outlier", True):
                values = self._repair_isolated_outliers(values)

            if preprocess.get("smooth", True):
                window = int(preprocess.get("smooth_window", 3))
                values = self._moving_average(values, window)

            if preprocess.get("normalize", True):
                values = self._z_score(values)

            for index, row in enumerate(out):
                row[col] = f"{values[index]:.10f}"

        return out

    def inject_digital_faults(
        self,
        normal_rows: list[dict[str, str]],
        feature_cols: list[str],
        simulation: dict[str, Any],
        label_col: str,
    ) -> list[dict[str, str]]:
        count = int(simulation.get("count", max(1, len(normal_rows) // 4)))
        fault_type = str(simulation.get("fault_type", "step")).lower()
        severity = float(simulation.get("severity", 3.0))
        feature = simulation.get("feature") or feature_cols[0]
        if feature not in feature_cols:
            raise ValueError(f"simulation feature '{feature}' not found in feature_cols")

        generated: list[dict[str, str]] = []
        for index in range(count):
            source = dict(normal_rows[index % len(normal_rows)])
            base_value = self._to_finite_float(source.get(feature))
            if fault_type == "ramp":
                value = base_value + severity * (index + 1) / max(1, count)
            elif fault_type == "intermittent":
                value = base_value + (severity if index % 2 == 0 else 0.0)
            else:
                value = base_value + severity

            source[feature] = f"{value:.10f}"
            source[label_col] = "1"
            source["fault_type"] = fault_type
            generated.append(source)
        return generated

    def inject_propagation_faults(
        self,
        normal_rows: list[dict[str, str]],
        feature_cols: list[str],
        simulation: dict[str, Any],
        label_col: str,
    ) -> list[dict[str, str]]:
        count = int(simulation.get("count", max(1, len(normal_rows) // 4)))
        source_feature = simulation.get("feature") or feature_cols[0]
        severity = float(simulation.get("severity", 2.0))
        propagation = simulation.get("propagation", {})

        if source_feature not in feature_cols:
            raise ValueError(f"simulation feature '{source_feature}' not found in feature_cols")

        generated: list[dict[str, str]] = []
        for index in range(count):
            row = dict(normal_rows[index % len(normal_rows)])
            base = self._to_finite_float(row.get(source_feature)) + severity
            row[source_feature] = f"{base:.10f}"

            for target, factor in propagation.items():
                if target not in feature_cols:
                    continue
                target_base = self._to_finite_float(row.get(target))
                row[target] = f"{(target_base + float(factor) * severity):.10f}"

            row[label_col] = "1"
            row["fault_type"] = "propagation"
            generated.append(row)
        return generated

    def extract_features_and_labels(
        self,
        rows: list[dict[str, str]],
        feature_cols: list[str],
        label_col: str | None,
    ) -> tuple[list[list[float]], list[int] | None]:
        features = [[float(row[column]) for column in feature_cols] for row in rows]
        if label_col is None:
            return features, None
        labels = [int(float(row[label_col])) for row in rows]
        return features, labels

    def _write_sqlite(self, file_path: Path, table_name: str, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        table = self._validate_sql_identifier(table_name)
        columns = [self._validate_sql_identifier(col) for col in rows[0].keys()]
        column_defs = ", ".join(f"{col} TEXT" for col in columns)
        placeholders = ", ".join("?" for _ in columns)
        insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

        values = [[str(row[col]) if row[col] is not None else None for col in columns] for row in rows]
        with sqlite3.connect(file_path) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({column_defs})")
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(insert_sql, values)
            conn.commit()

    def _validate_sql_identifier(self, name: str) -> str:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"Unsafe SQL identifier: {name}")
        return name

    def _to_finite_float(self, value: str | None) -> float:
        if value is None or value == "":
            return math.nan
        parsed = float(value)
        return parsed if math.isfinite(parsed) else math.nan

    def _fill_missing_with_neighbor_mean(self, values: list[float]) -> list[float]:
        out = list(values)
        for i, value in enumerate(out):
            if not math.isnan(value):
                continue
            prev_value = next((out[j] for j in range(i - 1, -1, -1) if not math.isnan(out[j])), None)
            next_value = next((out[j] for j in range(i + 1, len(out)) if not math.isnan(out[j])), None)
            if prev_value is None and next_value is None:
                out[i] = 0.0
            elif prev_value is None:
                out[i] = float(next_value)
            elif next_value is None:
                out[i] = float(prev_value)
            else:
                out[i] = (float(prev_value) + float(next_value)) / 2.0
        return out

    def _repair_isolated_outliers(self, values: list[float]) -> list[float]:
        if len(values) < 4:
            return values

        # 过滤掉 NaN 值，只用有效值计算统计量
        valid_values = [v for v in values if not math.isnan(v)]
        if len(valid_values) < 4:
            return values

        mean = sum(valid_values) / len(valid_values)
        variance = sum((x - mean) ** 2 for x in valid_values) / len(valid_values)
        std = math.sqrt(variance)
        if std == 0:
            return values

        sorted_values = sorted(valid_values)
        q1 = self._percentile(sorted_values, 25)
        q3 = self._percentile(sorted_values, 75)
        iqr = q3 - q1

        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr

        out = list(values)
        for i in range(1, len(values) - 1):
            value = values[i]
            # 跳过 NaN 值
            if math.isnan(value):
                continue
            is_sigma_outlier = abs(value - mean) > 3 * std
            is_iqr_outlier = value < low or value > high
            if is_sigma_outlier and is_iqr_outlier:
                # 替换值时也要处理可能的 NaN
                prev_val = values[i - 1] if not math.isnan(values[i - 1]) else mean
                next_val = values[i + 1] if not math.isnan(values[i + 1]) else mean
                out[i] = (prev_val + next_val) / 2.0
        return out

    def _moving_average(self, values: list[float], window: int) -> list[float]:
        if window <= 1:
            return values
        radius = window // 2
        out: list[float] = []
        for i in range(len(values)):
            left = max(0, i - radius)
            right = min(len(values), i + radius + 1)
            segment = values[left:right]
            out.append(sum(segment) / len(segment))
        return out

    def _z_score(self, values: list[float]) -> list[float]:
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = math.sqrt(variance)
        if std == 0:
            return [0.0 for _ in values]
        return [(x - mean) / std for x in values]

    def _percentile(self, sorted_values: list[float], p: float) -> float:
        if not sorted_values:
            return 0.0
        pos = (len(sorted_values) - 1) * p / 100.0
        low = int(math.floor(pos))
        high = int(math.ceil(pos))
        if low == high:
            return sorted_values[low]
        weight = pos - low
        return sorted_values[low] * (1 - weight) + sorted_values[high] * weight
