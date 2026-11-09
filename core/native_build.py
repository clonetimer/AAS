"""Isolated C/C++ build service for AAS native plugins.

The AAS web server is Python, but native plugin compilation deliberately uses
an *external system toolchain* (GCC/Clang/MSVC) in a temporary workspace.  The
compiler process does not inherit Python/venv/Conda package paths or user
CFLAGS/LDFLAGS, and the command is executed with ``shell=False``.

This is environment isolation, not a hostile-code sandbox.  A compiler can read
files that the AAS OS account can read, and a compiled DLL/SO is trusted native
code once installed.  Only compile/install trusted source code.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from typing import Any

from core.native_plugins import NATIVE_ABI_V1, NATIVE_PLUGIN_TYPES, native_library_suffixes, normalize_native_language


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_BUILD_LOG_CHARS = 24_000
MAX_LIBRARY_BYTES = 32 * 1024 * 1024
BUILD_TIMEOUT_SECONDS = 90

# Variables that can make a compiler accidentally consume Python environment
# headers/libraries or execute injected toolchain flags.  MSVC's INCLUDE/LIB
# are intentionally *not* removed because they are toolchain configuration,
# while Python-specific roots are removed from those variables separately.
_DROP_ENV_EXACT = {
    "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "PYTHONHOME",
    "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONBREAKPOINT",
    "PIP_CONFIG_FILE", "PIP_REQUIRE_VIRTUALENV", "PIP_TARGET", "PIP_PREFIX",
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH",
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
    "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
}
_DROP_ENV_PREFIXES = ("PYENV_", "POETRY_", "PIP_", "UV_", "CONDA_PYTHON_")


@dataclass(frozen=True)
class Toolchain:
    id: str
    label: str
    family: str
    c_compiler: str | None
    cpp_compiler: str | None
    version: str
    source: str = "system"
    msvc_env: tuple[tuple[str, str], ...] = ()

    def compiler_for(self, language: str) -> str | None:
        return self.c_compiler if language == "c" else self.cpp_compiler

    @property
    def languages(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.c_compiler:
            out.append("c")
        if self.cpp_compiler:
            out.append("cpp")
        return tuple(out)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "languages": list(self.languages),
            "version": self.version,
            "source": self.source,
            "isolated_from_python": True,
        }


def _norm_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _python_env_roots() -> tuple[str, ...]:
    """Return only *isolated* Python environment roots, never system /usr."""
    roots: list[str] = []
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        value = os.environ.get(key)
        if value:
            roots.append(_norm_path(value))
    # A venv changes sys.prefix while keeping sys.base_prefix at the base Python.
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    if sys.prefix and _norm_path(sys.prefix) != _norm_path(base_prefix):
        roots.append(_norm_path(sys.prefix))
    # Project-local virtual environments are also excluded even if AAS was
    # started through a wrapper that did not export VIRTUAL_ENV.
    try:
        project_root = Path(__file__).resolve().parents[1]
        for name in (".venv", "venv", "env"):
            path = project_root / name
            if path.exists():
                roots.append(_norm_path(path))
    except (OSError, RuntimeError):
        pass
    return tuple(dict.fromkeys(roots))


def _is_under(path: str | Path, roots: tuple[str, ...]) -> bool:
    target = _norm_path(path)
    for root in roots:
        root_sep = root.rstrip(os.sep) + os.sep
        if target == root or target.startswith(root_sep):
            return True
    return False


def _filter_path_list(value: str, roots: tuple[str, ...]) -> str:
    parts = []
    for part in (value or "").split(os.pathsep):
        if not part:
            continue
        if _is_under(part, roots):
            continue
        parts.append(part)
    return os.pathsep.join(parts)


def sanitized_native_environment(base: dict[str, str] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Build an environment for compiler processes without Python env leakage."""
    source = dict(os.environ if base is None else base)
    roots = _python_env_roots()
    removed: list[str] = []
    env: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper in _DROP_ENV_EXACT or any(upper.startswith(prefix) for prefix in _DROP_ENV_PREFIXES):
            removed.append(key)
            continue
        env[key] = value

    if "PATH" in env:
        clean = _filter_path_list(env["PATH"], roots)
        if clean != env["PATH"]:
            removed.append("PATH(Python-env entries)")
        env["PATH"] = clean

    # MSVC environment variables may contain paths into a Python/Conda env if
    # AAS was launched from such a shell.  Keep native SDK paths, remove only
    # Python-environment path segments.
    for key in ("INCLUDE", "LIB", "LIBPATH"):
        if key in env:
            env[key] = _filter_path_list(env[key], roots)

    env["AAS_NATIVE_BUILD"] = "1"
    env["AAS_NATIVE_ABI"] = NATIVE_ABI_V1
    report = {
        "python_env_roots_removed": list(roots),
        "removed_variables": sorted(set(removed)),
        "shell": False,
        "temporary_workspace": True,
        "arbitrary_user_flags": False,
    }
    return env, report


def _safe_executable(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        clean_env, _ = sanitized_native_environment()
        path = shutil.which(path, path=clean_env.get("PATH"))
        if not path:
            return None
        candidate = Path(path)
    try:
        candidate = candidate.resolve()
    except (OSError, RuntimeError):
        candidate = Path(os.path.abspath(candidate))
    if not candidate.is_file() or _is_under(candidate, _python_env_roots()):
        return None
    return str(candidate)


def _which_clean(name: str) -> str | None:
    env, _ = sanitized_native_environment()
    return _safe_executable(shutil.which(name, path=env.get("PATH")))


def _version_line(executable: str, family: str, env: dict[str, str] | None = None) -> str:
    try:
        if family == "msvc":
            cmd = [executable]
        else:
            cmd = [executable, "--version"]
        proc = subprocess.run(
            cmd,
            cwd=tempfile.gettempdir(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            shell=False,

        check=False,
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines[0][:240] if lines else Path(executable).name
    except (OSError, subprocess.SubprocessError):
        return Path(executable).name


def _configured_toolchain() -> Toolchain | None:
    cc_raw = os.environ.get("AAS_NATIVE_CC")
    cxx_raw = os.environ.get("AAS_NATIVE_CXX")
    if not cc_raw and not cxx_raw:
        return None
    cc = _safe_executable(cc_raw)
    cxx = _safe_executable(cxx_raw)
    if not cc and not cxx:
        return None
    probe = cxx or cc
    assert probe is not None
    env, _ = sanitized_native_environment()
    is_msvc = Path(probe).name.lower() in {"cl", "cl.exe"}
    family = "msvc" if is_msvc else "configured"
    return Toolchain(
        id="configured",
        label="管理员指定工具链",
        family=family,
        c_compiler=cc,
        cpp_compiler=cxx,
        version=_version_line(probe, "msvc" if is_msvc else "gcc", env),
        source="AAS_NATIVE_CC/AAS_NATIVE_CXX",
        msvc_env=tuple(env.items()) if is_msvc else (),
    )


def _find_vswhere() -> Path | None:
    candidates: list[Path] = []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe")
    found = _which_clean("vswhere.exe")
    if found:
        candidates.insert(0, Path(found))
    for path in candidates:
        if path.is_file() and not _is_under(path, _python_env_roots()):
            return path.resolve()
    return None


def _capture_msvc_environment(vcvars: Path) -> dict[str, str] | None:
    cmd_exe = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
    if not cmd_exe:
        return None
    # vcvars64.bat is discovered from a trusted Visual Studio installation;
    # no user-controlled source/flags are interpolated here.
    command = f'call "{vcvars}" >nul && set'
    try:
        proc = subprocess.run(
            [cmd_exe, "/d", "/s", "/c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,

        check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    parsed: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            parsed[key] = value
    clean, _ = sanitized_native_environment(parsed)
    return clean


def _discover_msvc() -> Toolchain | None:
    if os.name != "nt":
        return None

    # First honor an already initialized Developer Command Prompt.
    clean, _ = sanitized_native_environment()
    cl = _safe_executable(shutil.which("cl.exe", path=clean.get("PATH")))
    if cl:
        return Toolchain(
            id="msvc", label="MSVC", family="msvc",
            c_compiler=cl, cpp_compiler=cl,
            version=_version_line(cl, "msvc", clean),
            source="Developer Command Prompt",
            msvc_env=tuple(clean.items()),
        )

    vswhere = _find_vswhere()
    if vswhere is None:
        return None
    try:
        proc = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,

        check=False,
        )
        install = Path(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None
    vcvars = install / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.is_file():
        return None
    env = _capture_msvc_environment(vcvars)
    if not env:
        return None
    cl = _safe_executable(shutil.which("cl.exe", path=env.get("PATH")))
    if not cl:
        return None
    return Toolchain(
        id="msvc", label="MSVC", family="msvc",
        c_compiler=cl, cpp_compiler=cl,
        version=_version_line(cl, "msvc", env),
        source="Visual Studio Build Tools",
        msvc_env=tuple(env.items()),
    )


def _gnu_like_toolchain(tool_id: str, label: str, family: str, cc_name: str, cxx_name: str) -> Toolchain | None:
    cc = _which_clean(cc_name)
    cxx = _which_clean(cxx_name)
    if not cc and not cxx:
        return None
    probe = cxx or cc
    assert probe is not None
    env, _ = sanitized_native_environment()
    return Toolchain(
        id=tool_id,
        label=label,
        family=family,
        c_compiler=cc,
        cpp_compiler=cxx,
        version=_version_line(probe, family, env),
    )


@lru_cache(maxsize=1)
def detect_toolchains() -> tuple[Toolchain, ...]:
    tools: list[Toolchain] = []
    configured = _configured_toolchain()
    if configured:
        tools.append(configured)
    if os.name == "nt":
        msvc = _discover_msvc()
        if msvc:
            tools.append(msvc)
    gcc = _gnu_like_toolchain("gcc", "GCC / G++", "gcc", "gcc", "g++")
    if gcc:
        tools.append(gcc)
    clang = _gnu_like_toolchain("clang", "Clang / Clang++", "clang", "clang", "clang++")
    if clang:
        tools.append(clang)

    # Deduplicate by actual compiler pairs (configured may point to gcc/g++).
    unique: list[Toolchain] = []
    seen: set[tuple[str | None, str | None]] = set()
    for tool in tools:
        key = (tool.c_compiler, tool.cpp_compiler)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tool)
    return tuple(unique)


def compiler_catalog() -> list[dict[str, Any]]:
    return [tool.public() for tool in detect_toolchains()]


def _select_toolchain(toolchain_id: str, language: str) -> Toolchain:
    candidates = [tool for tool in detect_toolchains() if tool.compiler_for(language)]
    if not candidates:
        raise RuntimeError(
            "未检测到可用的系统 C/C++ 编译器。请安装 GCC/Clang/Visual Studio Build Tools，"
            "或继续使用“导入已编译库”。"
        )
    requested = str(toolchain_id or "auto").strip().lower()
    if requested in {"", "auto"}:
        return candidates[0]
    for tool in candidates:
        if tool.id == requested:
            return tool
    raise ValueError(f"编译器 {toolchain_id!r} 不可用或不支持 {language}")


def _safe_source_filename(filename: str, language: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename or ""):
        raise ValueError("源码文件名必须是普通文件名，不能包含目录")
    allowed = (".c",) if language == "c" else (".cpp", ".cc", ".cxx")
    if Path(name).suffix.lower() not in allowed:
        raise ValueError("C 源码必须使用 .c；C++ 源码必须使用 .cpp/.cc/.cxx")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("源码文件名仅允许字母、数字、点、下划线和连字符")
    return name


def _output_filename(source_filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_filename).stem).strip("._-") or "aas_plugin"
    suffix = native_library_suffixes()[0]
    if os.name != "nt" and not stem.startswith("lib"):
        stem = "lib" + stem
    return stem + suffix


def _build_command(
    tool: Toolchain,
    language: str,
    source_path: Path,
    output_path: Path,
    include_dir: Path,
) -> tuple[list[str], dict[str, str]]:
    compiler = tool.compiler_for(language)
    if not compiler:
        raise ValueError(f"{tool.label} 不支持 {language}")

    if tool.family == "msvc":
        env = dict(tool.msvc_env) if tool.msvc_env else sanitized_native_environment()[0]
        args = [compiler, "/nologo", "/LD", "/O2", "/W3"]
        if language == "cpp":
            args += ["/EHsc", "/std:c++17"]
        args += [f"/I{include_dir}", str(source_path), "/link", f"/OUT:{output_path}"]
        return args, env

    env, _ = sanitized_native_environment()
    args = [compiler]
    system = platform.system().lower()
    if system == "darwin":
        args += ["-dynamiclib"]
    else:
        args += ["-shared"]
    if os.name != "nt":
        args += ["-fPIC", "-fvisibility=hidden"]
    args += ["-O2", "-Wall", "-Wextra"]
    args += ["-std=c11" if language == "c" else "-std=c++17"]
    args += ["-I", str(include_dir), str(source_path), "-o", str(output_path)]
    if language == "c" and system != "darwin":
        args += ["-lm"]
    if system == "linux":
        args += ["-Wl,-z,defs", "-Wl,-z,relro,-z,now"]
    return args, env


def _redact_log(text: str, build_dir: Path) -> str:
    value = (text or "").replace(str(build_dir), "<build>")
    if len(value) > MAX_BUILD_LOG_CHARS:
        value = value[:MAX_BUILD_LOG_CHARS] + "\n…（编译日志已截断）"
    return value


def _validate_in_worker(root: Path, library_path: Path, ptype: str, env: dict[str, str]) -> dict[str, Any]:
    worker = root / "scripts" / "native_plugin_validator.py"
    if not worker.is_file():
        raise FileNotFoundError("缺少原生插件隔离校验器 scripts/native_plugin_validator.py")
    kind = "detector" if ptype == "detectors" else "metric"
    # -I -S gives the validator isolated mode and disables site initialization;
    # the script only imports stdlib ctypes/json and never imports AAS/numpy/third-party packages.
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(worker), "--library", str(library_path), "--kind", kind],
        cwd=str(library_path.parent),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        shell=False,

    check=False,
    )
    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        data = {}
    if proc.returncode != 0 or not data.get("ok"):
        reason = data.get("error") or proc.stderr.strip() or proc.stdout.strip() or "原生库隔离校验失败"
        raise RuntimeError(reason)
    return dict(data.get("details") or {})


def validate_native_library_isolated(
    root: str | Path,
    library_path: str | Path,
    ptype: str,
) -> dict[str, Any]:
    """Validate a DLL/SO in a short-lived stdlib-only process.

    The library is never loaded into the calling AAS server process. This is
    used by install/preview/discovery paths as well as the build service.
    """
    root = Path(root).resolve()
    library_path = Path(library_path).resolve()
    if ptype not in NATIVE_PLUGIN_TYPES:
        raise ValueError("C/C++ 原生插件当前仅支持检测算法和评估指标")
    if not library_path.is_file():
        raise FileNotFoundError(f"原生插件库不存在：{library_path}")
    env, _ = sanitized_native_environment()
    env.pop("AAS_NATIVE_BUILD", None)
    env["AAS_NATIVE_VALIDATION"] = "1"
    return _validate_in_worker(root, library_path, ptype, env)


def compile_native_source(
    root: str | Path,
    *,
    ptype: str,
    language: str,
    source_filename: str,
    source_content: str,
    toolchain: str = "auto",
) -> dict[str, Any]:
    """Compile one C/C++ plugin source file into an in-memory DLL/SO artifact."""
    root = Path(root).resolve()
    language = normalize_native_language(language)
    if language not in {"c", "cpp"}:
        raise ValueError("本机编译仅支持 C 或 C++")
    if ptype not in NATIVE_PLUGIN_TYPES:
        raise ValueError("C/C++ 原生插件当前仅支持检测算法和评估指标")
    source_filename = _safe_source_filename(source_filename, language)
    if not isinstance(source_content, str) or not source_content.strip():
        raise ValueError("源码内容不能为空")
    source_bytes = source_content.encode("utf-8")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ValueError(f"源码过大：最多允许 {MAX_SOURCE_BYTES // 1024} KB")
    if "\x00" in source_content:
        raise ValueError("源码不能包含 NUL 字符")

    selected = _select_toolchain(toolchain, language)
    compiler = selected.compiler_for(language)
    assert compiler is not None
    if _is_under(compiler, _python_env_roots()):
        raise RuntimeError("为保持与 Python 环境隔离，不允许使用位于 venv/Conda 中的编译器")

    env_report: dict[str, Any]
    if selected.family == "msvc" and selected.msvc_env:
        build_env = dict(selected.msvc_env)
        _, env_report = sanitized_native_environment(build_env)
    else:
        build_env, env_report = sanitized_native_environment()

    source_sha = hashlib.sha256(source_bytes).hexdigest()
    header_source = root / "native_plugins" / "aas_native_v1.h"
    if not header_source.is_file():
        raise FileNotFoundError("缺少 native_plugins/aas_native_v1.h")

    with tempfile.TemporaryDirectory(prefix="aas-native-build-") as tmp:
        build_dir = Path(tmp)
        include_dir = build_dir / "include"
        include_dir.mkdir()
        shutil.copy2(header_source, include_dir / "aas_native_v1.h")
        source_path = build_dir / source_filename
        source_path.write_bytes(source_bytes)
        output_name = _output_filename(source_filename)
        output_path = build_dir / output_name
        command, build_env = _build_command(selected, language, source_path, output_path, include_dir)
        try:
            proc = subprocess.run(
                command,
                cwd=str(build_dir),
                env=build_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=BUILD_TIMEOUT_SECONDS,
                shell=False,

            check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"编译超时（>{BUILD_TIMEOUT_SECONDS}s）") from exc
        stdout = _redact_log(proc.stdout, build_dir)
        stderr = _redact_log(proc.stderr, build_dir)
        if proc.returncode != 0:
            message = stderr.strip() or stdout.strip() or f"编译器退出码 {proc.returncode}"
            raise RuntimeError("编译失败：\n" + message)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("编译器未生成有效的动态库")
        if output_path.stat().st_size > MAX_LIBRARY_BYTES:
            raise RuntimeError(f"生成的动态库过大：最大允许 {MAX_LIBRARY_BYTES // (1024 * 1024)} MB")

        # Validate in a short-lived isolated worker so loading the just-built
        # native library cannot crash/poison the long-running AAS server during
        # the build step.
        validation = _validate_in_worker(root, output_path, ptype, build_env)
        binary = output_path.read_bytes()

    return {
        "status": "success",
        "built": True,
        "language": language,
        "abi": NATIVE_ABI_V1,
        "library_filename": output_name,
        "library_content_base64": base64.b64encode(binary).decode("ascii"),
        "size_bytes": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "source_sha256": source_sha,
        "compiler": selected.public(),
        "validation": validation,
        "logs": {"stdout": stdout, "stderr": stderr},
        "isolation": {
            **env_report,
            "compiler_outside_python_env": True,
            "validator": "python -I / stdlib-only worker / separate process",
            "build_workspace": "OS temporary directory",
        },
    }


def native_build_capabilities() -> dict[str, Any]:
    tools = compiler_catalog()
    return {
        "auto_compile": bool(tools),
        "compilers": tools,
        "build_isolation": {
            "external_toolchain_process": True,
            "shell": False,
            "python_env_variables_removed": True,
            "python_env_path_entries_removed": True,
            "user_cflags_ldflags_ignored": True,
            "temporary_workspace": True,
            "validator_separate_process": True,
            "runtime_isolation": True,
            "runtime_host": "separate process / python -I -S / stdlib-only ctypes host",
            "runtime_transport": "authenticated localhost IPC",
            "server_process_loads_native_library": False,
            "crash_containment": True,
            "note": "编译、ABI 校验和运行时均与 AAS Python 主服务进程隔离。原生插件在独立 Native Plugin Host 中执行；这可隔离崩溃/非法内存访问，但不是恶意代码权限沙箱。",
        },
    }
