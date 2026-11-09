from __future__ import annotations

import base64
import os
from pathlib import Path
import pickle
import tempfile
import unittest

import numpy as np

from core.native_build import compile_native_source, detect_toolchains
from core.native_host_client import NativeHostClient, NativeHostCrashedError, NativeHostTimeoutError
from detectors.native_detector import NativeDetector
from evaluator.metrics_plugins.base import MetricContext
from evaluator.metrics_plugins.native_metric import NativeMetric
from tests.test_native_build import C_METRIC, CPP_DETECTOR


ROOT = Path(__file__).resolve().parents[1]

CRASHING_C_METRIC = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#ifdef _WIN32
#define AAS_EXPORT __declspec(dllexport)
#else
#define AAS_EXPORT __attribute__((visibility("default")))
#endif
AAS_EXPORT const char* aas_plugin_abi(void) { return "aas_native_v1"; }
AAS_EXPORT const char* aas_plugin_kind(void) { return "metric"; }
AAS_EXPORT double aas_metric_compute(
    const int32_t* y, size_t yn,
    const double* score, size_t sn,
    const int32_t* pred, size_t pn,
    const double* train, size_t trn,
    const double* test, size_t ten,
    const char* cfg, int* ok) {
    (void)y; (void)yn; (void)score; (void)sn; (void)pred; (void)pn;
    (void)train; (void)trn; (void)test; (void)ten; (void)cfg; (void)ok;
    abort();
    return 0.0;
}
'''

HANGING_C_METRIC = r'''
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#define AAS_EXPORT __declspec(dllexport)
#else
#define AAS_EXPORT __attribute__((visibility("default")))
#endif
AAS_EXPORT const char* aas_plugin_abi(void) { return "aas_native_v1"; }
AAS_EXPORT const char* aas_plugin_kind(void) { return "metric"; }
AAS_EXPORT double aas_metric_compute(
    const int32_t* y, size_t yn,
    const double* score, size_t sn,
    const int32_t* pred, size_t pn,
    const double* train, size_t trn,
    const double* test, size_t ten,
    const char* cfg, int* ok) {
    (void)y; (void)yn; (void)score; (void)sn; (void)pred; (void)pn;
    (void)train; (void)trn; (void)test; (void)ten; (void)cfg; (void)ok;
    for (;;) { }
    return 0.0;
}
'''


class NativeRuntimeIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="aas-native-runtime-test-")
        cls.dir = Path(cls.tmp.name)
        tools = detect_toolchains()
        cls.have_c = any("c" in tool.languages for tool in tools)
        cls.have_cpp = any("cpp" in tool.languages for tool in tools)

        cls.metric_lib = None
        cls.detector_lib = None
        cls.crash_lib = None
        cls.hang_lib = None
        if cls.have_c:
            result = compile_native_source(
                ROOT, ptype="metrics", language="c",
                source_filename="runtime_metric.c", source_content=C_METRIC,
            )
            cls.metric_lib = cls.dir / result["library_filename"]
            cls.metric_lib.write_bytes(base64.b64decode(result["library_content_base64"]))
            crash = compile_native_source(
                ROOT, ptype="metrics", language="c",
                source_filename="runtime_crash_metric.c", source_content=CRASHING_C_METRIC,
            )
            cls.crash_lib = cls.dir / crash["library_filename"]
            cls.crash_lib.write_bytes(base64.b64decode(crash["library_content_base64"]))
            hang = compile_native_source(
                ROOT, ptype="metrics", language="c",
                source_filename="runtime_hang_metric.c", source_content=HANGING_C_METRIC,
            )
            cls.hang_lib = cls.dir / hang["library_filename"]
            cls.hang_lib.write_bytes(base64.b64decode(hang["library_content_base64"]))
        if cls.have_cpp:
            result = compile_native_source(
                ROOT, ptype="detectors", language="cpp",
                source_filename="runtime_detector.cpp", source_content=CPP_DETECTOR,
            )
            cls.detector_lib = cls.dir / result["library_filename"]
            cls.detector_lib.write_bytes(base64.b64decode(result["library_content_base64"]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_host_is_separate_stdlib_only_process(self):
        if not self.metric_lib:
            self.skipTest("No isolated C toolchain detected")
        with NativeHostClient(self.metric_lib, "metric") as host:
            info = host.runtime_info()
            self.assertNotEqual(info["pid"], os.getpid())
            self.assertTrue(info["isolated_python"])
            self.assertFalse(info["server_process_loads_library"])
            self.assertFalse(info["third_party_modules_loaded"])
            self.assertFalse(info["aas_modules_loaded"])

    def test_native_metric_runs_through_host(self):
        if not self.metric_lib:
            self.skipTest("No isolated C toolchain detected")
        metric = NativeMetric({
            "name": "runtime_metric", "label": "Runtime Metric",
            "language": "c", "abi": "aas_native_v1", "library": str(self.metric_lib),
            "requires": ["labels", "preds"], "dependencies": [],
        })
        try:
            value = metric.compute(MetricContext(y_true=[0, 1, 1], pred_label=[0, 0, 1]))
            self.assertAlmostEqual(value, 2 / 3)
            info = metric.runtime_info()
            self.assertNotEqual(info["pid"], os.getpid())
        finally:
            metric.close()

    def test_native_detector_state_survives_pickle_and_new_host(self):
        if not self.detector_lib:
            self.skipTest("No isolated C++ toolchain detected")
        descriptor = {
            "name": "runtime_detector", "label": "Runtime Detector",
            "language": "cpp", "abi": "aas_native_v1", "library": str(self.detector_lib),
            "dependencies": [], "config": {},
        }
        detector = NativeDetector({}, descriptor=descriptor)
        restored = None
        try:
            detector.fit([1.0, 2.0, 3.0])
            first_pid = detector.get_meta()["native_host_pid"]
            self.assertNotEqual(first_pid, os.getpid())
            self.assertEqual(detector.score([0.0, 2.0, 5.0]), [2.0, 0.0, 3.0])
            restored = pickle.loads(pickle.dumps(detector))
            second_pid = restored.get_meta()["native_host_pid"]
            self.assertNotEqual(first_pid, second_pid)
            self.assertEqual(restored.score([0.0, 2.0, 5.0]), [2.0, 0.0, 3.0])
        finally:
            detector.close()
            if restored is not None:
                restored.close()

    def test_native_abort_kills_only_host_not_test_process(self):
        if not self.crash_lib:
            self.skipTest("No isolated C toolchain detected")
        host = NativeHostClient(self.crash_lib, "metric", request_timeout=5)
        try:
            y = np.asarray([0, 1], dtype=np.int32)
            pred = np.asarray([0, 1], dtype=np.int32)
            with self.assertRaises(NativeHostCrashedError):
                host.metric_compute(
                    y_true=y.tobytes(), y_true_count=2,
                    scores=b"", scores_count=0,
                    preds=pred.tobytes(), preds_count=2,
                    train_values=b"", train_count=0,
                    test_values=b"", test_count=0,
                    config_json="{}",
                )
            # Reaching this assertion proves the parent Python process survived.
            self.assertEqual(os.getpid(), os.getpid())
        finally:
            host.close()

    def test_native_timeout_terminates_only_host(self):
        if not self.hang_lib:
            self.skipTest("No isolated C toolchain detected")
        host = NativeHostClient(self.hang_lib, "metric", request_timeout=0.4)
        try:
            with self.assertRaises(NativeHostTimeoutError):
                host.metric_compute(
                    y_true=b"", y_true_count=0,
                    scores=b"", scores_count=0,
                    preds=b"", preds_count=0,
                    train_values=b"", train_count=0,
                    test_values=b"", test_count=0,
                    config_json="{}",
                )
            self.assertFalse(host.alive)
        finally:
            host.close()


if __name__ == "__main__":
    unittest.main()
