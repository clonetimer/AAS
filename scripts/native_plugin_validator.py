"""Stdlib-only validator for a freshly built AAS native plugin.

Executed as ``python -I -S`` in a short-lived subprocess.  This file intentionally
must not import any AAS module or third-party Python package.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import c_char_p, c_size_t, c_void_p
import json
import os
from pathlib import Path

ABI = "aas_native_v1"


def _decode(value):
    if not value:
        return ""
    return value.decode("utf-8", errors="replace")


def validate(path: Path, kind: str) -> dict:
    _dll_dir = None
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        _dll_dir = os.add_dll_directory(str(path.parent))
    lib = ctypes.CDLL(str(path))
    required_common = ["aas_plugin_abi", "aas_plugin_kind"]
    required_detector = [
        "aas_detector_create", "aas_detector_destroy", "aas_detector_fit",
        "aas_detector_state_size", "aas_detector_export_state",
        "aas_detector_import_state",
    ]
    required_metric = ["aas_metric_compute"]
    required = required_common + (required_detector if kind == "detector" else required_metric)
    missing = [name for name in required if not hasattr(lib, name)]
    if missing:
        raise ValueError("原生插件缺少导出函数：" + ", ".join(missing))

    abi_fn = getattr(lib, "aas_plugin_abi")
    abi_fn.argtypes = []
    abi_fn.restype = c_char_p
    kind_fn = getattr(lib, "aas_plugin_kind")
    kind_fn.argtypes = []
    kind_fn.restype = c_char_p
    abi = _decode(abi_fn())
    actual_kind = _decode(kind_fn())
    if abi != ABI:
        raise ValueError(f"原生插件 ABI 不兼容：插件={abi or '(空)'}, 系统要求={ABI}")
    if actual_kind != kind:
        raise ValueError(f"原生插件类型不匹配：插件={actual_kind or '(空)'}, 当前类型={kind}")

    details = {
        "abi": abi,
        "kind": actual_kind,
        "required_symbols_ok": True,
        "loaded_in_server_process": False,
    }
    if kind == "detector":
        capabilities = {
            "score": hasattr(lib, "aas_detector_score"),
            "predict": hasattr(lib, "aas_detector_predict"),
            "threshold": hasattr(lib, "aas_detector_threshold"),
        }
        if not capabilities["score"] and not capabilities["predict"]:
            raise ValueError(
                "原生 detector 至少需要导出 aas_detector_score 或 aas_detector_predict"
            )
        details["capabilities"] = capabilities
        create = getattr(lib, "aas_detector_create")
        create.argtypes = [c_char_p]
        create.restype = c_void_p
        destroy = getattr(lib, "aas_detector_destroy")
        destroy.argtypes = [c_void_p]
        destroy.restype = None
        state_size = getattr(lib, "aas_detector_state_size")
        state_size.argtypes = [c_void_p]
        state_size.restype = c_size_t
        handle = create(b"{}")
        if not handle:
            raise ValueError("aas_detector_create 返回空指针")
        try:
            _ = int(state_size(handle))
        finally:
            destroy(handle)
        details["persistence"] = "native-state-v1"
    else:
        details["persistence"] = "not-applicable"
    return details


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--kind", choices=("detector", "metric"), required=True)
    args = parser.parse_args()
    try:
        details = validate(Path(args.library).resolve(), args.kind)
        print(json.dumps({"ok": True, "details": details}, ensure_ascii=False))
        return 0
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
