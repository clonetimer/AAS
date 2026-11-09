from __future__ import annotations

from importlib.util import find_spec
import subprocess
import sys
from typing import Dict, List, Tuple

# Module name -> pip distribution when they differ.
MODULE_TO_PACKAGE: Dict[str, str] = {
    "sklearn": "scikit-learn",
    "torch": "torch",
    "torchvision": "torchvision",
    "numpy": "numpy",
    "pandas": "pandas",
    "yaml": "PyYAML",
}


def _dependencies_for(detector_name: str) -> List[str]:
    """Resolve dependencies from detector YAML/metadata instead of a hardcoded map."""
    try:
        from detectors.registry import get_detector_dependencies

        return list(get_detector_dependencies(detector_name))
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def check_dependency(module_name: str) -> bool:
    """Return whether a dependency module is installed, without importing it.

    Dependency status APIs are catalog operations; importing PyTorch or
    scikit-learn here adds avoidable cold-start latency on Windows. The actual
    detector import remains the runtime validation point.
    """
    try:
        return find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def check_detector_dependencies(detector_name: str) -> Tuple[bool, List[str]]:
    dependencies = _dependencies_for(detector_name)
    missing = [dep for dep in dependencies if not check_dependency(dep)]
    return (not missing, missing)


def check_all_detectors() -> Dict[str, dict]:
    try:
        from detectors.registry import get_available_algorithms

        names = [item["name"] for item in get_available_algorithms()]
    except Exception:  # pylint: disable=broad-exception-caught
        names = []
    results: Dict[str, dict] = {}
    for detector_name in names:
        dependencies = _dependencies_for(detector_name)
        installed, missing = check_detector_dependencies(detector_name)
        results[detector_name] = {
            "installed": installed,
            "missing": missing,
            "dependencies": dependencies,
        }
    return results


def get_install_command(missing_packages: List[str]) -> str:
    pip_packages = [MODULE_TO_PACKAGE.get(pkg, pkg) for pkg in missing_packages]
    return f"pip install {' '.join(pip_packages)}"


def install_dependencies(missing_packages: List[str]) -> Tuple[bool, str]:
    if not missing_packages:
        return (True, "No missing dependencies")
    try:
        pip_packages = [MODULE_TO_PACKAGE.get(pkg, pkg) for pkg in missing_packages]
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *pip_packages],
            capture_output=True,
            text=True,
            timeout=300,

        check=False,
        )
        return (result.returncode == 0, result.stdout if result.returncode == 0 else result.stderr)
    except subprocess.TimeoutExpired:
        return (False, "Installation timed out")
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc))


def install_detector_dependencies(detector_name: str) -> Tuple[bool, str]:
    _, missing = check_detector_dependencies(detector_name)
    if not missing:
        return (True, "All dependencies are already installed")
    return install_dependencies(missing)


def install_all_missing_dependencies() -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}
    for detector_name, status in check_all_detectors().items():
        if not status["installed"]:
            results[detector_name] = install_detector_dependencies(detector_name)
    return results
