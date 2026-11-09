"""Resolve bound request dependencies; preserve the unbound test adapter."""
from __future__ import annotations
import sys
from typing import Any


def resolve_application() -> Any:
    """Legacy direct-handler fallback only; production binds server context."""
    for name in ("scripts.visualization_server", "visualization_server", "__main__"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "json_response") and hasattr(module, "ROOT"):
            return module
    return None


def main_from_compatibility_facade(handler=None):
    """Prefer the explicit per-server binding; retain existing test overrides."""
    server = getattr(handler, 'server', None)
    bound = getattr(server, 'aas_application', None)
    if bound is not None:
        return bound
    # Historical external code and test fixtures instantiate a plain
    # ThreadingHTTPServer with DashboardHandler but without a bound context.
    from scripts.server import handlers
    return handlers._get_main_module()
