"""Phase 34 routing/service boundaries; tests independent of seeded databases."""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

from scripts import visualization_server as app
from scripts.server import services, handlers
from scripts.server.handler_get import GetRoutesMixin
from scripts.server.handler_post import PostRoutesMixin
from scripts.server.evaluation_workflow import run_evaluation as evaluate_workflow

ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_entrypoints_preserved():
    assert issubclass(handlers.DashboardHandler, GetRoutesMixin)
    assert issubclass(handlers.DashboardHandler, PostRoutesMixin)
    assert callable(services.preview_import)
    assert callable(services.get_models)
    assert callable(services.execute_fault_injection)
    assert inspect.signature(app.run_evaluation).parameters['dataset'].name == 'dataset'
    assert 'app' in inspect.signature(evaluate_workflow).parameters


def test_runtime_resolution_does_not_scan_modules():
    assert handlers._get_main_module() is app


def test_service_path_override_retained_for_import_recognition(tmp_path):
    cfg = tmp_path / 'rules.yaml'
    cfg.write_text('version: 1\ncolumns:\n  timestamp: [clock_tick]\n', encoding='utf-8')
    with patch.object(services, 'DATA_IMPORT_ALIASES_PATH', cfg):
        services._reset_data_import_recognition_cache()
        result = services.preview_import('clock_tick,value,label\n1,5,0\n2,6,1\n', 'csv')
        assert result['suggested_mappings']['timestamp']['value'] == 'clock_tick'
        assert result['recognition_config']['source'] == 'yaml'
    services._reset_data_import_recognition_cache()


def test_active_ui_assets_untouched_by_server_refactor():
    html = (ROOT / 'web/visualization/browser.html').read_text(encoding='utf8')
    runner = (ROOT / 'web/visualization/runner.html').read_text(encoding='utf8')
    assert 'telemetry-v2-browser.js' in html
    assert 'runner-v2.js' in runner
    assert 'v2FaultDialog' in html


def test_legacy_service_module_is_narrow_facade():
    assert len((ROOT / 'scripts/server/services.py').read_text(encoding='utf8').splitlines()) < 250
    assert len((ROOT / 'scripts/server/handlers.py').read_text(encoding='utf8').splitlines()) < 300


def test_evaluation_workflow_uses_explicit_runtime_dependency():
    import pytest
    with patch.object(app, 'resolve_managed_database_path',
                      side_effect=RuntimeError('phase34-context-boundary')):
        with pytest.raises(RuntimeError, match='phase34-context-boundary'):
            app.run_evaluation(dataset='datasets/does_not_exist.db',
                               table='example', algorithm='zscore')
