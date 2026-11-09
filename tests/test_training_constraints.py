from __future__ import annotations

# pylint: disable=protected-access  # Tests intentionally exercise private validation before model fitting.

import unittest

from scripts.core.data_loader import DataBundle
from scripts.core.training import TrainingPipeline


class TrainingConstraintTests(unittest.TestCase):
    def _pipeline(self) -> TrainingPipeline:
        # _validate_training_data does not use the runtime container; bypass
        # __init__ so the test remains a fast pure business-rule test.
        return object.__new__(TrainingPipeline)

    def _bundle(self, labels, values=None) -> DataBundle:
        values = list(values if values is not None else range(1, len(labels) + 1))
        rows = [
            {"timestamp": str(i), "value": float(value), "label": label}
            for i, (value, label) in enumerate(zip(values, labels))
        ]
        numeric = [float(v) for v in values]
        return DataBundle(
            train_rows=rows,
            test_rows=list(rows),
            train_values=numeric,
            test_values=list(numeric),
            mode="simple",
            train_value_rows=list(rows),
            test_value_rows=list(rows),
        )

    def test_normal_training_data_passes(self):
        result = self._pipeline()._validate_training_data(
            "three_sigma", self._bundle([0, 0, 0])
        )
        self.assertTrue(result["normal_only"])
        self.assertEqual(result["fault_count"], 0)

    def test_fault_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "仅包含正常样本"):
            self._pipeline()._validate_training_data(
                "three_sigma", self._bundle([0, 1, 0])
            )

    def test_too_few_samples_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "训练数据量不足"):
            self._pipeline()._validate_training_data(
                "three_sigma", self._bundle([0])
            )

    def test_iqr_requires_four_samples(self):
        with self.assertRaisesRegex(ValueError, "至少需要 4 条"):
            self._pipeline()._validate_training_data(
                "iqr", self._bundle([0, 0, 0])
            )

    def test_missing_label_is_rejected(self):
        bundle = self._bundle([0, 0])
        for row in bundle.train_rows:
            row.pop("label", None)
        with self.assertRaisesRegex(ValueError, "缺少 label 字段"):
            self._pipeline()._validate_training_data("three_sigma", bundle)


if __name__ == "__main__":
    unittest.main()
