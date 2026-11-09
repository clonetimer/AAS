"""Process-isolated runtime client for AAS C/C++ plugins.

Native DLL/SO code is *not* loaded into the long-running AAS web/worker
process.  Instead each active native detector/metric is executed in a small
stdlib-only ``python -I`` helper process and called over an authenticated local
IPC connection.

This provides crash / illegal-memory-access containment for the Python service,
and keeps the native runtime independent from AAS/NumPy/Torch imports.  It is
still not a hostile-code sandbox: the host process runs under the same OS user
and a malicious plugin can access resources available to that account.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from core.native_build import sanitized_native_environment


DEFAULT_START_TIMEOUT = float(os.environ.get("AAS_NATIVE_HOST_START_TIMEOUT", "10"))
DEFAULT_REQUEST_TIMEOUT = float(os.environ.get("AAS_NATIVE_HOST_REQUEST_TIMEOUT", "120"))
MAX_RUNTIME_BLOB_BYTES = int(os.environ.get("AAS_NATIVE_HOST_MAX_BLOB_MB", "128")) * 1024 * 1024


class NativeHostError(RuntimeError):
    """Base error raised by the process-isolated native runtime."""


class NativeHostCrashedError(NativeHostError):
    """The native host process exited unexpectedly (e.g. native crash)."""


class NativeHostTimeoutError(NativeHostError):
    """A native operation exceeded the configured runtime timeout."""


@dataclass(frozen=True)
class NativeHostStatus:
    pid: int
    kind: str
    isolated_python: bool
    server_process_loads_library: bool
    transport: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "kind": self.kind,
            "isolated_python": self.isolated_python,
            "server_process_loads_library": self.server_process_loads_library,
            "transport": self.transport,
        }


def _runtime_environment() -> tuple[dict[str, str], dict[str, Any]]:
    env, report = sanitized_native_environment()
    env.pop("AAS_NATIVE_BUILD", None)
    env["AAS_NATIVE_RUNTIME_HOST"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    report = dict(report)
    report.update({
        "python_isolated_flag": True,
        "site_initialization_disabled": True,
        "aas_modules_in_host": False,
        "third_party_python_packages_required": False,
    })
    return env, report


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_blob(payload: bytes | bytearray | memoryview | None, label: str) -> bytes:
    if payload is None:
        return b""
    value = bytes(payload)
    if len(value) > MAX_RUNTIME_BLOB_BYTES:
        raise ValueError(
            f"{label} 数据过大：{len(value) / (1024 * 1024):.1f} MB，"
            f"单次 IPC 上限为 {MAX_RUNTIME_BLOB_BYTES // (1024 * 1024)} MB"
        )
    return value


class NativeHostClient:
    """Own one isolated native-plugin host process."""

    def __init__(
        self,
        library_path: str | Path,
        kind: str,
        *,
        config_json: str = "{}",
        request_timeout: float | None = None,
        start_timeout: float | None = None,
    ):
        if kind not in {"detector", "metric"}:
            raise ValueError(f"不支持的原生插件类型: {kind}")
        self.library_path = Path(library_path).resolve()
        if not self.library_path.is_file():
            raise FileNotFoundError(f"原生插件库不存在：{self.library_path}")
        self.kind = kind
        self.config_json = str(config_json or "{}")
        if len(self.config_json.encode("utf-8")) > 512 * 1024:
            raise ValueError("原生插件配置 JSON 过大")
        self.request_timeout = DEFAULT_REQUEST_TIMEOUT if request_timeout is None else float(request_timeout)
        self.start_timeout = DEFAULT_START_TIMEOUT if start_timeout is None else float(start_timeout)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._conn = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._status: NativeHostStatus | None = None
        self._env_report: dict[str, Any] = {}
        self._closed = False
        self._start()

    @property
    def pid(self) -> int | None:
        return self._status.pid if self._status is not None else (self._proc.pid if self._proc else None)

    @property
    def alive(self) -> bool:
        return bool(self._proc is not None and self._proc.poll() is None and self._conn is not None and not self._closed)

    @property
    def status(self) -> dict[str, Any]:
        base = self._status.as_dict() if self._status is not None else {
            "pid": self.pid,
            "kind": self.kind,
            "isolated_python": True,
            "server_process_loads_library": False,
            "transport": "authenticated-local-ipc",
        }
        base["environment"] = dict(self._env_report)
        return base

    def _start(self) -> None:
        worker = _project_root() / "scripts" / "native_plugin_host.py"
        if not worker.is_file():
            raise FileNotFoundError("缺少运行时隔离宿主 scripts/native_plugin_host.py")

        self._tmp = tempfile.TemporaryDirectory(prefix="aas-native-host-")
        tmpdir = Path(self._tmp.name)
        handshake = tmpdir / "handshake.json"
        error_file = tmpdir / "error.txt"
        auth = secrets.token_bytes(32)
        env, self._env_report = _runtime_environment()

        args = [
            sys.executable, "-I", "-S", str(worker),
            "--library", str(self.library_path),
            "--kind", self.kind,
            "--auth", auth.hex(),
            "--handshake", str(handshake),
            "--error-file", str(error_file),
        ]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(self.library_path.parent),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        if os.name == "nt":
            flags = 0
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True

        self._proc = subprocess.Popen(args, **popen_kwargs)
        deadline = time.monotonic() + max(0.5, self.start_timeout)
        info: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if handshake.is_file():
                try:
                    info = json.loads(handshake.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeError):
                    info = None
                if info:
                    break
            if self._proc.poll() is not None:
                break
            time.sleep(0.02)

        if not info or not info.get("ok"):
            message = "原生插件隔离宿主启动失败"
            if error_file.is_file():
                text = error_file.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    message += "：" + text[:2000]
            self._terminate()
            raise NativeHostCrashedError(message)

        try:
            port = int(info["port"])
            self._conn = Client(("127.0.0.1", port), authkey=auth)
        except Exception as exc:
            self._terminate()
            raise NativeHostCrashedError(f"无法连接原生插件隔离宿主：{exc}") from exc

        init = self._request_raw({"op": "init", "config_json": self.config_json}, timeout=self.start_timeout)
        status = init.get("runtime") or {}
        self._status = NativeHostStatus(
            pid=int(status.get("pid") or info.get("pid") or self._proc.pid),
            kind=self.kind,
            isolated_python=bool(status.get("isolated_python", True)),
            server_process_loads_library=False,
            transport=str(status.get("transport") or "authenticated-local-ipc"),
        )

    def _crash_message(self, operation: str, exc: Exception | None = None) -> str:
        code = self._proc.poll() if self._proc is not None else None
        suffix = f"，退出码={code}" if code is not None else ""
        if exc is not None:
            suffix += f"，{type(exc).__name__}: {exc}"
        return f"原生插件宿主在执行 {operation} 时异常退出{suffix}；AAS 主服务未加载该 DLL/SO。"

    def _request_raw(self, message: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        timeout = self.request_timeout if timeout is None else float(timeout)
        operation = str(message.get("op") or "request")
        with self._lock:
            if self._closed or self._conn is None or self._proc is None:
                raise NativeHostCrashedError("原生插件隔离宿主已关闭")
            if self._proc.poll() is not None:
                raise NativeHostCrashedError(self._crash_message(operation))
            try:
                self._conn.send(message)
                if not self._conn.poll(max(0.01, timeout)):
                    self._terminate()
                    raise NativeHostTimeoutError(
                        f"原生插件操作 {operation} 超时（>{timeout:g}s），隔离宿主已终止，AAS 主服务继续运行。"
                    )
                response = self._conn.recv()
            except (EOFError, BrokenPipeError, ConnectionError, OSError) as exc:
                self._terminate()
                raise NativeHostCrashedError(self._crash_message(operation, exc)) from exc
            if not isinstance(response, dict):
                raise NativeHostError(f"原生插件宿主返回了非法响应：{type(response).__name__}")
            if not response.get("ok"):
                raise NativeHostError(str(response.get("error") or f"原生插件操作 {operation} 失败"))
            result = response.get("result")
            return dict(result) if isinstance(result, dict) else {"value": result}

    def runtime_info(self) -> dict[str, Any]:
        return self._request_raw({"op": "runtime_info"})

    def detector_fit(self, values: bytes, count: int) -> None:
        blob = _safe_blob(values, "训练")
        self._request_raw({"op": "detector_fit", "data": blob, "count": int(count)})

    def detector_score_optional(self, values: bytes, count: int) -> tuple[bool, bytes]:
        blob = _safe_blob(values, "评分")
        result = self._request_raw({"op": "detector_score", "data": blob, "count": int(count)})
        if not result.get("available"):
            return False, b""
        return True, _safe_blob(result.get("data", b""), "评分结果")

    def detector_score(self, values: bytes, count: int) -> bytes:
        available, payload = self.detector_score_optional(values, count)
        if not available:
            raise NativeHostError("原生 detector 未导出 aas_detector_score")
        return payload

    def detector_predict(self, values: bytes, count: int) -> tuple[bool, bytes]:
        blob = _safe_blob(values, "判定")
        result = self._request_raw({"op": "detector_predict", "data": blob, "count": int(count)})
        if not result.get("available"):
            return False, b""
        return True, _safe_blob(result.get("data", b""), "判定结果")

    def detector_threshold(self, scores: bytes, count: int) -> tuple[bool, float | None]:
        blob = _safe_blob(scores, "阈值")
        result = self._request_raw({"op": "detector_threshold", "data": blob, "count": int(count)})
        if not result.get("available"):
            return False, None
        return True, float(result.get("value"))

    def detector_export_state(self) -> bytes:
        result = self._request_raw({"op": "detector_export_state"})
        return _safe_blob(result.get("data", b""), "模型状态")

    def detector_import_state(self, payload: bytes) -> None:
        blob = _safe_blob(payload, "模型状态")
        self._request_raw({"op": "detector_import_state", "data": blob})

    def metric_compute(
        self,
        *,
        y_true: bytes,
        y_true_count: int,
        scores: bytes,
        scores_count: int,
        preds: bytes,
        preds_count: int,
        train_values: bytes,
        train_count: int,
        test_values: bytes,
        test_count: int,
        config_json: str,
    ) -> float:
        result = self._request_raw({
            "op": "metric_compute",
            "y_true": _safe_blob(y_true, "y_true"), "y_true_count": int(y_true_count),
            "scores": _safe_blob(scores, "scores"), "scores_count": int(scores_count),
            "preds": _safe_blob(preds, "preds"), "preds_count": int(preds_count),
            "train_values": _safe_blob(train_values, "train_values"), "train_count": int(train_count),
            "test_values": _safe_blob(test_values, "test_values"), "test_count": int(test_count),
            "config_json": str(config_json or "{}"),
        })
        return float(result["value"])

    def _terminate(self) -> None:
        conn, proc = self._conn, self._proc
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if proc is not None and proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    # Best effort: taskkill /T also terminates descendants if a
                    # plugin spawned helper processes. Fall back to terminate().
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, shell=False, timeout=3,

                        check=False,
                        )
                    except Exception:  # pylint: disable=broad-exception-caught
                        proc.terminate()
            except Exception:  # pylint: disable=broad-exception-caught
                try:
                    proc.kill()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            try:
                proc.wait(timeout=3)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        self._proc = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._conn is not None and self._proc is not None and self._proc.poll() is None:
                try:
                    self._request_raw({"op": "close"}, timeout=2.0)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            self._closed = True
            self._terminate()
            if self._tmp is not None:
                try:
                    self._tmp.cleanup()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                self._tmp = None

    def __enter__(self) -> "NativeHostClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self):  # pragma: no cover - best effort during interpreter teardown
        try:
            self.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
