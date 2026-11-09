from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

import numpy as np

from detectors.native_detector import NativeDetector
from core.native_build import (
    compile_native_source,
    detect_toolchains,
    sanitized_native_environment,
)


ROOT = Path(__file__).resolve().parents[1]


C_METRIC = r'''
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
    (void)score; (void)sn; (void)train; (void)trn; (void)test; (void)ten; (void)cfg;
    if (!ok || !y || !pred || yn == 0 || yn != pn) { if (ok) *ok = 0; return 0.0; }
    size_t hit = 0;
    for (size_t i = 0; i < yn; ++i) if (y[i] == pred[i]) ++hit;
    *ok = 1;
    return (double)hit / (double)yn;
}
'''


CPP_DETECTOR = r'''
#include <cstddef>
#include <cstring>
#include <new>
#ifdef _WIN32
#define AAS_EXPORT __declspec(dllexport)
#else
#define AAS_EXPORT __attribute__((visibility("default")))
#endif
struct State { double mean = 0.0; };
extern "C" {
AAS_EXPORT const char* aas_plugin_abi() { return "aas_native_v1"; }
AAS_EXPORT const char* aas_plugin_kind() { return "detector"; }
AAS_EXPORT void* aas_detector_create(const char*) { return new (std::nothrow) State(); }
AAS_EXPORT void aas_detector_destroy(void* p) { delete static_cast<State*>(p); }
AAS_EXPORT int aas_detector_fit(void* p, const double* x, std::size_t n) {
    if (!p || !x || !n) return 1; double total = 0.0;
    for (std::size_t i = 0; i < n; ++i) total += x[i];
    static_cast<State*>(p)->mean = total / static_cast<double>(n); return 0;
}
AAS_EXPORT int aas_detector_score(void* p, const double* x, std::size_t n, double* out) {
    if (!p || !x || !out) return 2; double mean = static_cast<State*>(p)->mean;
    for (std::size_t i = 0; i < n; ++i) out[i] = x[i] > mean ? x[i] - mean : mean - x[i]; return 0;
}
AAS_EXPORT std::size_t aas_detector_state_size(void*) { return sizeof(double); }
AAS_EXPORT int aas_detector_export_state(void* p, unsigned char* out, std::size_t n) {
    if (!p || !out || n < sizeof(double)) return 3;
    std::memcpy(out, &static_cast<State*>(p)->mean, sizeof(double)); return 0;
}
AAS_EXPORT int aas_detector_import_state(void* p, const unsigned char* in, std::size_t n) {
    if (!p || !in || n != sizeof(double)) return 4;
    std::memcpy(&static_cast<State*>(p)->mean, in, sizeof(double)); return 0;
}
}
'''


CPP_LABEL_ONLY_DETECTOR = r"""
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#ifdef _WIN32
#define AAS_EXPORT __declspec(dllexport)
#else
#define AAS_EXPORT __attribute__((visibility("default")))
#endif
struct State { double mean = 0.0; };
extern "C" {
AAS_EXPORT const char* aas_plugin_abi() { return "aas_native_v1"; }
AAS_EXPORT const char* aas_plugin_kind() { return "detector"; }
AAS_EXPORT void* aas_detector_create(const char*) { return new (std::nothrow) State(); }
AAS_EXPORT void aas_detector_destroy(void* p) { delete static_cast<State*>(p); }
AAS_EXPORT int aas_detector_fit(void* p, const double* x, std::size_t n) {
    if (!p || !x || !n) return 1; double total = 0.0;
    for (std::size_t i = 0; i < n; ++i) total += x[i];
    static_cast<State*>(p)->mean = total / static_cast<double>(n); return 0;
}
AAS_EXPORT int aas_detector_predict(void* p, const double* x, std::size_t n, int32_t* out) {
    if (!p || !x || !out) return 2; double mean = static_cast<State*>(p)->mean;
    for (std::size_t i = 0; i < n; ++i) out[i] = x[i] > mean ? 1 : 0; return 0;
}
AAS_EXPORT std::size_t aas_detector_state_size(void*) { return sizeof(double); }
AAS_EXPORT int aas_detector_export_state(void* p, unsigned char* out, std::size_t n) {
    if (!p || !out || n < sizeof(double)) return 3;
    std::memcpy(out, &static_cast<State*>(p)->mean, sizeof(double)); return 0;
}
AAS_EXPORT int aas_detector_import_state(void* p, const unsigned char* in, std::size_t n) {
    if (!p || !in || n != sizeof(double)) return 4;
    std::memcpy(&static_cast<State*>(p)->mean, in, sizeof(double)); return 0;
}
}
"""


class NativeBuildIsolationTests(unittest.TestCase):
    def test_sanitized_environment_drops_python_and_user_flags(self):
        base = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "/tmp/py",
            "PYTHONHOME": "/tmp/python",
            "VIRTUAL_ENV": "/tmp/venv",
            "CONDA_PREFIX": "/tmp/conda",
            "CFLAGS": "-include evil.h",
            "CXXFLAGS": "-fplugin=evil.so",
            "LDFLAGS": "-Wl,evil",
            "HOME": "/tmp/home",
        }
        env, report = sanitized_native_environment(base)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
            self.assertNotIn(key, env)
        self.assertEqual(env["HOME"], "/tmp/home")
        self.assertFalse(report["shell"])
        self.assertFalse(report["arbitrary_user_flags"])

    def test_source_filename_rejects_shell_like_text(self):
        with self.assertRaises(ValueError):
            compile_native_source(
                ROOT,
                ptype="metrics",
                language="c",
                source_filename="metric.c;touch-pwned",
                source_content=C_METRIC,
            )

    def test_compile_c_metric_when_toolchain_available(self):
        if not any("c" in tool.languages for tool in detect_toolchains()):
            self.skipTest("No isolated C toolchain detected")
        result = compile_native_source(
            ROOT,
            ptype="metrics",
            language="c",
            source_filename="aas_test_metric.c",
            source_content=C_METRIC,
        )
        self.assertTrue(result["built"])
        self.assertEqual(result["validation"]["kind"], "metric")
        self.assertFalse(result["validation"]["loaded_in_server_process"])
        self.assertTrue(base64.b64decode(result["library_content_base64"], validate=True))
        self.assertTrue(result["isolation"]["compiler_outside_python_env"])

    def test_compile_cpp_detector_when_toolchain_available(self):
        if not any("cpp" in tool.languages for tool in detect_toolchains()):
            self.skipTest("No isolated C++ toolchain detected")
        result = compile_native_source(
            ROOT,
            ptype="detectors",
            language="cpp",
            source_filename="aas_test_detector.cpp",
            source_content=CPP_DETECTOR,
        )
        self.assertTrue(result["built"])
        self.assertEqual(result["validation"]["kind"], "detector")
        self.assertEqual(result["validation"]["persistence"], "native-state-v1")


    def test_native_label_mode_can_omit_score_when_toolchain_available(self):
        if not any("cpp" in tool.languages for tool in detect_toolchains()):
            self.skipTest("No isolated C++ toolchain detected")
        result = compile_native_source(
            ROOT,
            ptype="detectors",
            language="cpp",
            source_filename="aas_label_only_detector.cpp",
            source_content=CPP_LABEL_ONLY_DETECTOR,
        )
        self.assertEqual(
            result["validation"]["capabilities"],
            {"score": False, "predict": True, "threshold": False},
        )
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / result["library_filename"]
            library.write_bytes(base64.b64decode(result["library_content_base64"]))
            detector = NativeDetector(
                {"decision_mode": "label"},
                descriptor={
                    "name": "label_only",
                    "language": "cpp",
                    "abi": "aas_native_v1",
                    "library": str(library),
                    "decision_mode": "label",
                },
            )
            try:
                detector.fit(np.array([[0.0], [1.0], [2.0]]))
                detected = detector.detect(np.array([[0.1], [3.0]]))
                self.assertIsNone(detected["anomaly_score"])
                self.assertIsNone(detected["threshold"])
                self.assertEqual(detected["pred_label"], [0, 1])
                self.assertEqual(detected["decision_mode"], "label")
            finally:
                detector.close()


if __name__ == "__main__":
    unittest.main()
