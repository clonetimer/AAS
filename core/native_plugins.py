"""Native C/C++ plugin bridge for AAS.

The public native ABI is deliberately a *C ABI* even for C++ plugins.  C++
implementations export the functions with ``extern \"C\"`` so the host is not
coupled to compiler-specific C++ name mangling or object layouts.

Phase 1 supports the two extension points where native code is most useful:

* detector plugins (training + scoring + persisted native state)
* metric plugins (one-shot metric computation)

Native plugins are trusted local code. Runtime detector/metric adapters execute
DLL/SO code in a separate Native Plugin Host process; this module still keeps a
low-level ``NativeLibrary`` loader for the stdlib validator/compatibility paths.
Only load artifacts from trusted sources.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, c_char_p, c_double, c_int, c_int32, c_size_t, c_ubyte, c_void_p
import os
from pathlib import Path
import platform
from typing import Any, Sequence

import numpy as np


NATIVE_ABI_V1 = "aas_native_v1"
NATIVE_LANGUAGES = {"c", "cpp", "c++", "native"}
NATIVE_PLUGIN_TYPES = {"detectors", "metrics"}


def normalize_native_language(value: Any) -> str:
    raw = str(value or "python").strip().lower()
    if raw == "c++":
        return "cpp"
    if raw == "native":
        return "cpp"
    return raw


def is_native_descriptor(descriptor: dict[str, Any] | None) -> bool:
    if not isinstance(descriptor, dict):
        return False
    language = normalize_native_language(descriptor.get("language"))
    return language in {"c", "cpp"}


def native_library_suffixes() -> tuple[str, ...]:
    if os.name == "nt":
        return (".dll",)
    if platform.system().lower() == "darwin":
        return (".dylib", ".so")
    return (".so",)


def native_library_hint() -> str:
    if os.name == "nt":
        return ".dll"
    if platform.system().lower() == "darwin":
        return ".dylib/.so"
    return ".so"


def _project_root_from_descriptor_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / "configs").is_dir():
            return candidate
    return None


def resolve_native_library_path(
    descriptor: dict[str, Any],
    *,
    project_root: str | Path | None = None,
    descriptor_path: str | Path | None = None,
) -> Path:
    raw = str(descriptor.get("library") or descriptor.get("library_path") or "").strip()
    if not raw:
        raise ValueError("原生插件 YAML 必须声明 library")

    requested = Path(raw)
    candidates: list[Path] = []
    if requested.is_absolute():
        candidates.append(requested)
    else:
        if project_root is not None:
            candidates.append(Path(project_root) / requested)
        desc_path = Path(descriptor_path) if descriptor_path is not None else None
        inferred_root = _project_root_from_descriptor_path(desc_path)
        if inferred_root is not None:
            candidates.append(inferred_root / requested)
        if desc_path is not None:
            candidates.append(desc_path.resolve().parent / requested)
        candidates.append(Path.cwd() / requested)

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    display = ", ".join(str(p) for p in seen) or raw
    raise FileNotFoundError(f"找不到原生插件库 {raw!r}；已检查：{display}")


def _flatten_float(values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    return np.ascontiguousarray(array.reshape(-1), dtype=np.float64)


def _int_array(values: Sequence[int] | None) -> np.ndarray | None:
    if values is None:
        return None
    return np.ascontiguousarray(np.asarray(values, dtype=np.int32).reshape(-1), dtype=np.int32)


def _double_array(values: Sequence[float] | None) -> np.ndarray | None:
    if values is None:
        return None
    return np.ascontiguousarray(np.asarray(values, dtype=np.float64).reshape(-1), dtype=np.float64)


def _array_ptr(array: np.ndarray | None, ctype: Any):
    if array is None or array.size == 0:
        return None
    return array.ctypes.data_as(POINTER(ctype))


class NativeLibrary:
    """Load one AAS native library and bind its ABI symbols."""

    def __init__(self, path: str | Path, expected_kind: str):
        self.path = Path(path).resolve()
        self.expected_kind = expected_kind
        self._cdll: ctypes.CDLL | None = None
        self._closed = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"原生插件库不存在：{self.path}")
        self._dll_dir = None
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(str(self.path.parent))
        try:
            self._cdll = ctypes.CDLL(str(self.path))
        except OSError as exc:
            raise RuntimeError(f"无法加载原生插件库 {self.path.name}: {exc}") from exc

        self.abi = self._bind("aas_plugin_abi", [], c_char_p)
        self.kind = self._bind("aas_plugin_kind", [], c_char_p)
        self.last_error = self._bind("aas_plugin_last_error", [], c_char_p, required=False)

        abi_value = self._decode(self.abi())
        if abi_value != NATIVE_ABI_V1:
            self.close()
            raise ValueError(f"原生插件 ABI 不兼容：插件={abi_value or '(空)'}, 系统要求={NATIVE_ABI_V1}")
        kind_value = self._decode(self.kind())
        if kind_value != self.expected_kind:
            self.close()
            raise ValueError(f"原生插件类型不匹配：插件={kind_value or '(空)'}, 当前安装类型={self.expected_kind}")

        if self.expected_kind == "detector":
            self.create = self._bind("aas_detector_create", [c_char_p], c_void_p)
            self.destroy = self._bind("aas_detector_destroy", [c_void_p], None)
            self.fit = self._bind("aas_detector_fit", [c_void_p, POINTER(c_double), c_size_t], c_int)
            self.score = self._bind(
                "aas_detector_score",
                [c_void_p, POINTER(c_double), c_size_t, POINTER(c_double)],
                c_int,
                required=False,
            )
            self.predict = self._bind(
                "aas_detector_predict",
                [c_void_p, POINTER(c_double), c_size_t, POINTER(c_int32)],
                c_int,
                required=False,
            )
            self.state_size = self._bind("aas_detector_state_size", [c_void_p], c_size_t)
            self.export_state = self._bind(
                "aas_detector_export_state",
                [c_void_p, POINTER(c_ubyte), c_size_t],
                c_int,
            )
            self.import_state = self._bind(
                "aas_detector_import_state",
                [c_void_p, POINTER(c_ubyte), c_size_t],
                c_int,
            )
            self.threshold = self._bind(
                "aas_detector_threshold",
                [c_void_p, POINTER(c_double), c_size_t],
                c_double,
                required=False,
            )
        elif self.expected_kind == "metric":
            self.metric_compute = self._bind(
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
            self.close()
            raise ValueError(f"不支持的原生插件 kind: {self.expected_kind}")

    @staticmethod
    def _decode(value: bytes | None) -> str:
        if not value:
            return ""
        return value.decode("utf-8", errors="replace")

    def _bind(self, name: str, argtypes: list[Any], restype: Any, *, required: bool = True):
        assert self._cdll is not None
        try:
            fn = getattr(self._cdll, name)
        except AttributeError as exc:
            if required:
                self.close()
                raise ValueError(f"原生插件缺少导出函数：{name}") from exc
            return None
        fn.argtypes = argtypes
        fn.restype = restype
        return fn

    def error_text(self, fallback: str) -> str:
        fn = getattr(self, "last_error", None)
        if fn is not None:
            try:
                text = self._decode(fn())
                if text:
                    return text
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return fallback

    def close(self) -> None:
        if self._closed:
            return
        cdll = self._cdll
        self._cdll = None
        self._closed = True
        if cdll is None:
            return
        handle = getattr(cdll, "_handle", None)
        if not handle:
            return
        try:
            import _ctypes
            free_library: Any = getattr(_ctypes, "FreeLibrary", None)
            dlclose: Any = getattr(_ctypes, "dlclose", None)
            if os.name == "nt" and callable(free_library):
                free_library(handle)  # pylint: disable=not-callable
            elif callable(dlclose):
                dlclose(handle)
        except Exception:  # pylint: disable=broad-exception-caught
            # Process teardown and some Python builds do not expose an explicit
            # unload primitive.  The OS will release the module on process exit.
            pass

    def __del__(self):  # pragma: no cover - best effort at interpreter teardown
        try:
            self.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def validate_native_library(path: str | Path, ptype: str) -> dict[str, Any]:
    """Low-level in-process ABI validator.

    Server-facing preview/install/discovery code must use
    ``core.native_build.validate_native_library_isolated`` instead so an invalid
    native library cannot crash the AAS service.
    """
    if ptype not in NATIVE_PLUGIN_TYPES:
        raise ValueError("C/C++ 原生插件首期仅支持检测算法和评估指标")
    kind = "detector" if ptype == "detectors" else "metric"
    library = NativeLibrary(path, kind)
    details: dict[str, Any] = {
        "abi": NATIVE_ABI_V1,
        "kind": kind,
        "library": str(Path(path).resolve()),
        "required_symbols_ok": True,
    }
    try:
        if kind == "detector":
            handle = library.create(b"{}")
            if not handle:
                raise RuntimeError(library.error_text("aas_detector_create 返回空指针"))
            try:
                size = int(library.state_size(handle))
                if size < 0:
                    raise RuntimeError("aas_detector_state_size 返回非法值")
            finally:
                library.destroy(handle)
            details["persistence"] = "native-state-v1"
        else:
            details["persistence"] = "not-applicable"
    finally:
        library.close()
    return details



def native_descriptor_contract_errors(ptype: str, descriptor: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if ptype not in NATIVE_PLUGIN_TYPES:
        errors.append("C/C++ 原生插件首期仅支持检测算法和评估指标")
        return errors
    language = normalize_native_language(descriptor.get("language"))
    if language not in {"c", "cpp"}:
        errors.append("原生插件 language 必须为 c 或 cpp")
    abi = str(descriptor.get("abi") or "").strip()
    if abi != NATIVE_ABI_V1:
        errors.append(f"原生插件 abi 必须为 {NATIVE_ABI_V1}")
    if not str(descriptor.get("library") or "").strip():
        errors.append("原生插件 YAML 必须声明 library")
    if ptype == "metrics":
        requires = descriptor.get("requires", [])
        if requires is not None and not isinstance(requires, list):
            errors.append("评估指标 requires 必须是列表")
    return errors
