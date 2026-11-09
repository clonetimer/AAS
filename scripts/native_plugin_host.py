"""Stdlib-only process host for AAS native C/C++ plugins.

Launched with ``python -I`` by :mod:`core.native_host_client`.  This worker
intentionally imports no AAS module and no third-party package.  Native code is
loaded here via ctypes, not in the long-running AAS server process.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import POINTER, c_char_p, c_double, c_int, c_int32, c_size_t, c_ubyte, c_void_p
import json
from multiprocessing.connection import Listener
import os
from pathlib import Path
import sys
import traceback

ABI = "aas_native_v1"
MAX_BLOB = int(os.environ.get("AAS_NATIVE_HOST_MAX_BLOB_MB", "128")) * 1024 * 1024


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _apply_limits() -> dict:
    report = {"resource_limits": "best-effort"}
    if os.name == "nt":
        report["platform_limits"] = "windows-process-boundary"
        return report
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(256, hard if hard != resource.RLIM_INFINITY else 256)
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, target))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        memory_mb = int(os.environ.get("AAS_NATIVE_HOST_MEMORY_MB", "0") or 0)
        if memory_mb > 0 and hasattr(resource, "RLIMIT_AS"):
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            report["memory_mb"] = memory_mb
        cpu_seconds = int(os.environ.get("AAS_NATIVE_HOST_CPU_SECONDS", "0") or 0)
        if cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            report["cpu_seconds"] = cpu_seconds
        report["core_dump"] = False
        report["nofile"] = 256
    except Exception as exc:  # pylint: disable=broad-exception-caught
        report["limit_warning"] = f"{type(exc).__name__}: {exc}"
    return report


def _decode(value) -> str:
    return value.decode("utf-8", errors="replace") if value else ""


def _check_blob(data, count: int, item_size: int, label: str) -> bytes:
    if data is None:
        data = b""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} 必须是二进制数据")
    value = bytes(data)
    if len(value) > MAX_BLOB:
        raise ValueError(f"{label} 超过 IPC 数据上限")
    if int(count) < 0 or len(value) != int(count) * item_size:
        raise ValueError(f"{label} 长度与 count 不一致")
    return value


def _f64(data, count: int, label: str):
    raw = _check_blob(data, count, ctypes.sizeof(c_double), label)
    if count == 0:
        return None, None
    arr = (c_double * count).from_buffer_copy(raw)
    return arr, ctypes.cast(arr, POINTER(c_double))


def _i32(data, count: int, label: str):
    raw = _check_blob(data, count, ctypes.sizeof(c_int32), label)
    if count == 0:
        return None, None
    arr = (c_int32 * count).from_buffer_copy(raw)
    return arr, ctypes.cast(arr, POINTER(c_int32))


class NativeRuntime:
    def __init__(self, path: Path, kind: str):
        self.path = path
        self.kind_expected = kind
        self._dll_dir = None
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(str(path.parent))
        self.lib = ctypes.CDLL(str(path))
        self.instance = None
        self._bind_common()
        self._bind_kind()

    def _fn(self, name, args, result, required=True):
        try:
            fn = getattr(self.lib, name)
        except AttributeError as exc:
            if required:
                raise ValueError(f"原生插件缺少导出函数：{name}") from exc
            return None
        fn.argtypes = args
        fn.restype = result
        return fn

    def _bind_common(self):
        self.abi_fn = self._fn("aas_plugin_abi", [], c_char_p)
        self.kind_fn = self._fn("aas_plugin_kind", [], c_char_p)
        self.last_error_fn = self._fn("aas_plugin_last_error", [], c_char_p, required=False)
        abi = _decode(self.abi_fn())
        kind = _decode(self.kind_fn())
        if abi != ABI:
            raise ValueError(f"原生插件 ABI 不兼容：插件={abi or '(空)'}, 系统要求={ABI}")
        if kind != self.kind_expected:
            raise ValueError(f"原生插件类型不匹配：插件={kind or '(空)'}, 当前类型={self.kind_expected}")

    def _bind_kind(self):
        if self.kind_expected == "detector":
            self.create = self._fn("aas_detector_create", [c_char_p], c_void_p)
            self.destroy = self._fn("aas_detector_destroy", [c_void_p], None)
            self.fit = self._fn("aas_detector_fit", [c_void_p, POINTER(c_double), c_size_t], c_int)
            self.score = self._fn(
                "aas_detector_score", [c_void_p, POINTER(c_double), c_size_t, POINTER(c_double)], c_int,
                required=False
            )
            self.predict = self._fn(
                "aas_detector_predict", [c_void_p, POINTER(c_double), c_size_t, POINTER(c_int32)], c_int,
                required=False
            )
            self.state_size = self._fn("aas_detector_state_size", [c_void_p], c_size_t)
            self.export_state = self._fn(
                "aas_detector_export_state", [c_void_p, POINTER(c_ubyte), c_size_t], c_int
            )
            self.import_state = self._fn(
                "aas_detector_import_state", [c_void_p, POINTER(c_ubyte), c_size_t], c_int
            )
            self.threshold = self._fn(
                "aas_detector_threshold", [c_void_p, POINTER(c_double), c_size_t], c_double, required=False
            )
        elif self.kind_expected == "metric":
            self.metric_compute = self._fn(
                "aas_metric_compute",
                [
                    POINTER(c_int32), c_size_t,
                    POINTER(c_double), c_size_t,
                    POINTER(c_int32), c_size_t,
                    POINTER(c_double), c_size_t,
                    POINTER(c_double), c_size_t,
                    c_char_p, POINTER(c_int),
                ],
                c_double,
            )
        else:
            raise ValueError(f"不支持的 kind: {self.kind_expected}")

    def error(self, fallback: str) -> str:
        if self.last_error_fn is not None:
            try:
                text = _decode(self.last_error_fn())
                if text:
                    return text
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return fallback

    def init(self, config_json: str):
        if self.kind_expected != "detector":
            return
        handle = self.create(str(config_json or "{}").encode("utf-8"))
        if not handle:
            raise RuntimeError(self.error("aas_detector_create 返回空指针"))
        self.instance = int(handle)

    def require_instance(self):
        if not self.instance:
            raise RuntimeError("原生 detector 尚未初始化")
        return c_void_p(self.instance)

    def close(self):
        if self.kind_expected == "detector" and self.instance:
            handle = self.instance
            self.instance = None
            try:
                self.destroy(c_void_p(handle))
            except Exception:  # pylint: disable=broad-exception-caught
                pass


def _handle(runtime: NativeRuntime, message: dict) -> dict:
    op = str(message.get("op") or "")
    if op == "runtime_info":
        return {
            "pid": os.getpid(),
            "isolated_python": bool(getattr(sys.flags, "isolated", 0)),
            "transport": "authenticated-local-ipc",
            "server_process_loads_library": False,
            "third_party_modules_loaded": any(name in sys.modules for name in ("numpy", "torch", "sklearn")),
            "aas_modules_loaded": any(name == "core" or name.startswith("core.") or name.startswith("detectors.") for name in sys.modules),
            "detector_has_score": bool(getattr(runtime, "score", None)) if runtime.kind_expected == "detector" else None,
            "detector_has_predict": bool(getattr(runtime, "predict", None)) if runtime.kind_expected == "detector" else None,
            "detector_has_threshold": bool(getattr(runtime, "threshold", None)) if runtime.kind_expected == "detector" else None,
        }
    if op == "detector_fit":
        handle = runtime.require_instance()
        count = int(message.get("count") or 0)
        _arr, ptr = _f64(message.get("data"), count, "训练数据")
        rc = int(runtime.fit(handle, ptr, count))
        if rc != 0:
            raise RuntimeError(runtime.error(f"aas_detector_fit 失败，状态码 {rc}"))
        return {}
    if op == "detector_score":
        handle = runtime.require_instance()
        if runtime.score is None:
            return {"available": False}
        count = int(message.get("count") or 0)
        _arr, ptr = _f64(message.get("data"), count, "评分数据")
        out = (c_double * count)()
        out_ptr = ctypes.cast(out, POINTER(c_double)) if count else None
        rc = int(runtime.score(handle, ptr, count, out_ptr))
        if rc != 0:
            raise RuntimeError(runtime.error(f"aas_detector_score 失败，状态码 {rc}"))
        return {"available": True, "data": bytes(out)}
    if op == "detector_predict":
        handle = runtime.require_instance()
        if runtime.predict is None:
            return {"available": False}
        count = int(message.get("count") or 0)
        _arr, ptr = _f64(message.get("data"), count, "判定数据")
        out = (c_int32 * count)()
        out_ptr = ctypes.cast(out, POINTER(c_int32)) if count else None
        rc = int(runtime.predict(handle, ptr, count, out_ptr))
        if rc != 0:
            raise RuntimeError(runtime.error(f"aas_detector_predict 失败，状态码 {rc}"))
        return {"available": True, "data": bytes(out)}
    if op == "detector_threshold":
        handle = runtime.require_instance()
        if runtime.threshold is None:
            return {"available": False}
        count = int(message.get("count") or 0)
        _arr, ptr = _f64(message.get("data"), count, "阈值数据")
        return {"available": True, "value": float(runtime.threshold(handle, ptr, count))}
    if op == "detector_export_state":
        handle = runtime.require_instance()
        size = int(runtime.state_size(handle))
        if size < 0 or size > MAX_BLOB:
            raise RuntimeError("aas_detector_state_size 返回非法大小")
        if size == 0:
            return {"data": b""}
        out = (c_ubyte * size)()
        rc = int(runtime.export_state(handle, out, size))
        if rc != 0:
            raise RuntimeError(runtime.error(f"aas_detector_export_state 失败，状态码 {rc}"))
        return {"data": bytes(out)}
    if op == "detector_import_state":
        handle = runtime.require_instance()
        raw = bytes(message.get("data") or b"")
        if len(raw) > MAX_BLOB:
            raise ValueError("模型状态超过 IPC 数据上限")
        if not raw:
            return {}
        buf = (c_ubyte * len(raw)).from_buffer_copy(raw)
        rc = int(runtime.import_state(handle, buf, len(raw)))
        if rc != 0:
            raise RuntimeError(runtime.error(f"aas_detector_import_state 失败，状态码 {rc}"))
        return {}
    if op == "metric_compute":
        y_count = int(message.get("y_true_count") or 0)
        s_count = int(message.get("scores_count") or 0)
        p_count = int(message.get("preds_count") or 0)
        tr_count = int(message.get("train_count") or 0)
        te_count = int(message.get("test_count") or 0)
        _y_arr, y_ptr = _i32(message.get("y_true"), y_count, "y_true")
        _s_arr, s_ptr = _f64(message.get("scores"), s_count, "scores")
        _p_arr, p_ptr = _i32(message.get("preds"), p_count, "preds")
        _tr_arr, tr_ptr = _f64(message.get("train_values"), tr_count, "train_values")
        _te_arr, te_ptr = _f64(message.get("test_values"), te_count, "test_values")
        ok = c_int(0)
        value = runtime.metric_compute(
            y_ptr, y_count, s_ptr, s_count, p_ptr, p_count,
            tr_ptr, tr_count, te_ptr, te_count,
            str(message.get("config_json") or "{}").encode("utf-8"), ctypes.byref(ok),
        )
        if ok.value != 1:
            raise RuntimeError(runtime.error("aas_metric_compute 返回失败状态"))
        return {"value": float(value)}
    raise ValueError(f"未知原生宿主操作：{op}")


def serve(args) -> int:
    handshake = Path(args.handshake).resolve()
    error_file = Path(args.error_file).resolve()
    listener = None
    conn = None
    runtime = None
    try:
        auth = bytes.fromhex(args.auth)
        listener = Listener(("127.0.0.1", 0), family="AF_INET", authkey=auth)
        port = int(listener.address[1])
        _write(handshake, json.dumps({"ok": True, "port": port, "pid": os.getpid()}, ensure_ascii=False))
        conn = listener.accept()
        listener.close()
        listener = None
        limits = _apply_limits()

        # The library is deliberately loaded *after* the authenticated parent
        # connects and after the ready handshake has been written.  If loading
        # crashes, the parent sees EOF/exit rather than losing the AAS service.
        runtime = NativeRuntime(Path(args.library).resolve(), args.kind)

        initialized = False
        while True:
            message = conn.recv()
            if not isinstance(message, dict):
                conn.send({"ok": False, "error": "IPC 请求必须是对象"})
                continue
            op = str(message.get("op") or "")
            if op == "init":
                if initialized:
                    conn.send({"ok": False, "error": "原生宿主已经初始化"})
                    continue
                runtime.init(str(message.get("config_json") or "{}"))
                initialized = True
                conn.send({"ok": True, "result": {
                    "runtime": {
                        "pid": os.getpid(),
                        "isolated_python": bool(getattr(sys.flags, "isolated", 0)),
                        "transport": "authenticated-local-ipc",
                        "server_process_loads_library": False,
                        "resource_limits": limits,
                    }
                }})
                continue
            if not initialized:
                conn.send({"ok": False, "error": "原生宿主尚未初始化"})
                continue
            if op == "close":
                conn.send({"ok": True, "result": {}})
                break
            try:
                result = _handle(runtime, message)
                conn.send({"ok": True, "result": result})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 0
    except (EOFError, BrokenPipeError, ConnectionResetError):
        return 0
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        # Native SIGSEGV/abort cannot reach here; the process simply exits and
        # the parent reports NativeHostCrashedError. Python-side host failures
        # are recorded for startup diagnostics.
        try:
            _write(error_file, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}")
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return 2
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if listener is not None:
            try:
                listener.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--kind", choices=("detector", "metric"), required=True)
    parser.add_argument("--auth", required=True)
    parser.add_argument("--handshake", required=True)
    parser.add_argument("--error-file", required=True)
    return serve(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
