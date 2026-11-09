"""Regression tests for normalized single-channel table selection and model inputs."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.server import services
from scripts.server.model_source_contract import model_input_sources

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_multifeature_not_a_standard_train_or_test_source():
    tables = {item['name']: item for item in services.sqlite_tables(ROOT / 'datasets/history_sample.db')}
    assert tables['DEMO_multifeature_train']['data_role'] == 'train'
    assert tables['DEMO_multifeature_train']['is_single_measurement'] is False
    assert tables['DEMO_multifeature_test']['is_single_measurement'] is False
    assert tables['DEMO_train_temperature']['is_single_measurement'] is True
    assert tables['DEMO_test_temperature']['is_single_measurement'] is True
    assert all(item['is_single_measurement'] for item in tables.values()
               if item['name'].startswith('GF1_') and item['data_role'] == 'train')


def _mock_main(config, *, source_path=None):
    return SimpleNamespace(
        load_model_config=lambda _: config,
        resolve_managed_model_dir=lambda _: 'models/mock',
        resolve_managed_database_path=lambda _: source_path or ROOT / 'datasets/history_sample.db',
        sqlite_tables=services.sqlite_tables,
    )


def test_single_model_identity_resolved_from_hierarchy_catalog():
    model = {'id': 7, 'file_path': 'models/mock', 'dataset': 'history_sample.db',
             'table_name': 'DEMO_train_temperature'}
    sources = model_input_sources(_mock_main({'config': {'data_contract': {}}}), model)
    assert sources['complete']
    assert sources['sources'][0]['identity'] == {
        'model': 'DEMO', 'subsystem': 'ADCS', 'device': 'demo_unit', 'variable': 'temperature',
    }


def test_multisource_contract_preserves_model_feature_order_if_train_db_missing(tmp_path):
    model = {'id': 9, 'file_path': 'models/mock', 'dataset': 'deleted.db', 'table_name': 'T'}
    def identity(channel):
        return {'model': 'M', 'subsystem': 'S', 'device': 'D', 'variable': channel}
    config = {'config': {'data_contract': {
        'source_tables': ['train_current', 'train_temp'],
        'alignment': {'source_signatures': {'train_temp': identity('temperature'),
                                             'train_current': identity('current')}},
        'use_clean_data': True,
    }}}
    sources = model_input_sources(_mock_main(config, source_path=tmp_path/'missing.db'), model)
    assert sources['complete']
    assert [entry['identity']['variable'] for entry in sources['sources']] == ['current', 'temperature']
    assert sources['data_view'] == 'clean'


def test_old_model_missing_source_identity_must_not_claim_compatibility(tmp_path):
    model = {'id': 11, 'file_path': 'models/mock', 'dataset': 'gone.db', 'table_name': 'gone_table'}
    sources = model_input_sources(_mock_main({'config': {}}, source_path=tmp_path/'missing.db'), model)
    assert not sources['complete']
    assert sources['sources'][0]['identity'] is None


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js unavailable')
def test_real_runner_table_selection_in_node_harness():
    result = subprocess.run(['node', 'tests/runner_selection_behavior.cjs'], cwd=ROOT,
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '5 scenarios passed' in result.stdout


def test_model_contract_http_get_and_invalid_id():
    """Actual authenticated HTTP routing returns only identities, not model paths."""
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from unittest.mock import patch
    from tests.http_test_handler import AuthorizedTestHandler
    from scripts import visualization_server

    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    model = {'id': 8, 'file_path': 'models/mock', 'dataset': 'history_sample.db',
             'table_name': 'DEMO_train_temperature'}
    try:
        with (patch.object(visualization_server, 'get_model', return_value=model),
              patch.object(visualization_server, 'resolve_managed_model_dir', return_value='models/mock'),
              patch.object(visualization_server, 'load_model_config', return_value={'config':{}})):
            with urlopen(base + '/api/model-input-sources?model_id=8', timeout=10) as resp:
                output = json.load(resp)
            assert output['complete']
            assert output['sources'][0]['identity']['variable'] == 'temperature'
            assert 'file_path' not in output
            with pytest.raises(HTTPError) as bad:
                urlopen(base + '/api/model-input-sources?model_id=abc', timeout=10)
            assert bad.value.code == 400
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_http_training_rejects_legacy_multifeature_wide_table():
    """Bypassing the front-end does not re-enable wide-table standard training."""
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from tests.http_test_handler import AuthorizedTestHandler

    server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/api/train-model'
        request = Request(url, data=json.dumps({
            'dataset': 'history_sample.db', 'table': 'DEMO_multifeature_train',
            'algorithm': 'zscore', 'evaluationMode': 'simple',
        }).encode('utf-8'), method='POST', headers={'Content-Type':'application/json'})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=10)
        assert error.value.code == 400
        message = json.loads(error.value.read().decode('utf-8'))['error']
        assert '训练' in message
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_persisted_logical_multi_identity_survives_missing_original_database(tmp_path):
    model = {'id': 25, 'file_path': 'models/mock', 'dataset':'deleted.db', 'table_name':'speed_train_0'}
    speed = {'model':'SAT','subsystem':'ADCS','device':'RW1','variable':'speed'}
    current = {**speed, 'variable':'current'}
    contract = {'logical_multi_sources':[
        {'identity':speed,'tables':['speed_train_0','speed_train_1']},
        {'identity':current,'tables':['current_train_0','current_train_1']},
    ]}
    resolved = model_input_sources(_mock_main({'config':{'data_contract':contract}},
                                              source_path=tmp_path/'nonexistent.db'), model)
    assert resolved['selection_mode'] == 'logical_multi'
    assert resolved['complete']
    assert [item['identity']['variable'] for item in resolved['sources']] == ['speed','current']
    assert [item['table'] for item in resolved['sources']] == ['speed_train_0','current_train_0']
