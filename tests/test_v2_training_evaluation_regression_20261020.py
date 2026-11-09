"""Regression tests for the V2-only training/evaluation contract.

Small synthetic V2 data deliberately has authoritative train/test roles and
shared physical identity. Uploaded 19 simulation runs do NOT have these roles.
"""
from pathlib import Path
import json
import sqlite3
import shutil
import shutil
from unittest.mock import Mock

import pytest

from detectors.registry import get_available_algorithms, get_detector_config_schema
from scripts.server.v2_algorithm_parameters import validate_algorithm_configuration
from scripts.server.telemetry_v2_training import load_matrix, validate_training_selection
from scripts.core.training import TrainingPipeline
from scripts.core.model_manager import set_models_dir, load_model, validate_saved_model
from tests.test_telemetry_v2_training_phase5b import build


def test_algorithms_have_available_schema_and_parameter_validation():
    options={x['name']:x for x in get_available_algorithms()}
    for algo in ['three_sigma','zscore','iqr','isolation_forest_detector','mlp_detector','lstm_autoencoder_detector']:
        assert algo in options
        schema=get_detector_config_schema(algo)
        assert schema['parameters']
        defaults={p['name']:p.get('default') for p in schema['parameters']}
        assert validate_algorithm_configuration(algo,defaults)==defaults
    assert validate_algorithm_configuration('isolation_forest_detector',{'n_estimators':13,'random_state':42})['n_estimators']==13
    for data in [{'n_estimators':0},{'n_estimators':True},{'n_estimators':'not a number'},
                 {'contamination':0.9},{'threshold_method':'nonexistent'},{'nonexistent_param':1}]:
        with pytest.raises(ValueError):
            validate_algorithm_configuration('isolation_forest_detector',data)
    with pytest.raises(ValueError): validate_algorithm_configuration('not_registered',{})
    with pytest.raises(ValueError): validate_algorithm_configuration('mlp_detector',{'hidden_sizes':[-1,32]})


def test_v2_runner_exposes_detector_parameters_and_sends_payload():
    web=Path(__file__).resolve().parents[1]/'web/visualization'
    html=(web/'runner.html').read_text('utf8')
    js=(web/'runner-v2.js').read_text('utf8')
    assert 'id="trainAlgorithmParams"' in html
    assert '/api/algorithm-config-schema?' in js
    assert 'algorithm_params:collectParameters()' in js
    assert '/api/v2/training/start' in js
    assert '/api/v2/evaluation/run' in js
    assert 'tableSelect' not in html


def test_v2_end_to_end_train_persist_restore_evaluate(tmp_path):
    path=build(tmp_path)
    ids=[1,2]
    train,source=load_matrix(path,channel_ids=ids,role='train')
    assert len(train)==20
    request={'channel_ids':ids,'method':'exact','tolerance_seconds':0,
             'algorithm':'isolation_forest_detector','algorithm_params':{'n_estimators':12,'contamination':0.1},
             'train_ratio':0.7,'normalization_method':'zscore'}
    assert validate_training_selection(path,request)['role']=='train'
    data_contract={
      'version':6, 'feature_columns':['value','source_2'],
      'v2_channel_ids':ids,'v2_role':'train','v2_expected_sources':source['sources'],
      'v2_alignment':{'method':'exact','tolerance_seconds':0},
      'model_input_scaling':{'enabled':True,'method':'zscore'},
      'time_selection':{'column':'timestamp','start':None,'end':None}}
    set_models_dir(tmp_path/'model_artifacts')
    trained=TrainingPipeline(path,limit=0,train_ratio=0.7).run('isolation_forest_detector',
      table='v2_channels',algorithm_params=request['algorithm_params'],data_contract=data_contract)
    modeldir=Path(trained.model_info['model_dir'])
    assert modeldir.is_dir()
    config=json.loads((modeldir/'config.json').read_text(encoding='utf-8'))
    stored=config['config']['data_contract']
    assert stored['v2_channel_ids']==ids
    assert stored['feature_columns']==['value','source_2']
    assert stored['model_input_scaling']['parameters']['per_feature']
    assert config['config']['algorithm_params']['n_estimators']==12
    restored=validate_saved_model(modeldir,'isolation_forest_detector',experiment_id=trained.experiment_id)
    _,test_source=load_matrix(path,channel_ids=ids,role='test',expected=source['sources'])
    assert test_source['aligned_count']==20
    restored.fit=Mock(side_effect=AssertionError('evaluation must not refit'))
    evaluation_contract={**stored,'v2_role':'test','v2_expected_sources':source['sources'],
                         'model_input_scaling':stored['model_input_scaling']}
    result=TrainingPipeline(path,limit=0,train_ratio=1.0).run('isolation_forest_detector',
       table='v2_channels',persist_model=False,preloaded_detector=restored,
       data_contract=evaluation_contract)
    restored.fit.assert_not_called()
    assert result.metrics.mode=='supervised'
    assert result.metrics.total_samples==20
    assert {x['label'] for x in result.result_rows}=={0,1}
    assert not result.model_info
    # Contract rejects swapped channel order even if the series are otherwise valid.
    with pytest.raises(ValueError,match='身份|时基'):
        load_matrix(path,channel_ids=[2,1],role='test',expected=source['sources'])
    # Changing test values never fits or overwrites persisted training scaler.
    assert json.loads((modeldir/'config.json').read_text(encoding='utf-8'))['config']['data_contract']['model_input_scaling']==stored['model_input_scaling']


def test_real_uploaded_database_not_improperly_promoted_to_training(tmp_path):
    # Optional: direct read-only verification against the exact user artifact.
    external=Path('/mnt/data/AAS_Phase7_targets_labeled_v2_20261020.sqlite')
    if not external.is_file(): pytest.skip('prebuilt database not available')
    conn=sqlite3.connect('file:'+str(external)+'?mode=ro',uri=True)
    try:
        assert conn.execute('SELECT COUNT(*) FROM v2_channels').fetchone()[0]==1064
        assert conn.execute("SELECT COUNT(*) FROM v2_segments WHERE role='train'").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM v2_segments WHERE role='test'").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM v2_segments WHERE role='simulation'").fetchone()[0]==1064
        assert conn.execute("SELECT COUNT(*) FROM v2_run_annotations WHERE split='train'").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM v2_run_annotations WHERE split='validation'").fetchone()[0] > 0
    finally: conn.close()


def test_v2_algorithm_parameter_http_contract(tmp_path,monkeypatch):
    """Live HTTP endpoints must expose schema and reject invalid config pre-task."""
    import json as _json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from scripts import visualization_server as main
    from tests.http_test_handler import AuthorizedTestHandler

    db=build(tmp_path)
    (tmp_path/'datasets').mkdir()
    # HTTP route only needs a readable database file, not a symlink.
    # Windows standard users cannot create symlinks without Developer Mode.
    shutil.copy2(db, tmp_path/'datasets'/'aas_telemetry_v2.sqlite')
    monkeypatch.setattr(main,'ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def fetch(route,payload=None):
        req=Request(base+route,data=_json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type':'application/json'})
        try: r=urlopen(req,timeout=10)
        except HTTPError as err: r=err
        with r: return r.status,_json.load(r)
    try:
        code,algorithms=fetch('/api/available-algorithms')
        assert code==200 and 'isolation_forest_detector' in {x['name'] for x in algorithms['algorithms']}
        code,schema=fetch('/api/algorithm-config-schema?algorithm=isolation_forest_detector')
        assert code==200 and any(p['name']=='n_estimators' for p in schema['schema']['parameters'])
        code,error=fetch('/api/v2/training/start',{'channel_ids':[1], 'algorithm':'isolation_forest_detector',
             'algorithm_params':{'n_estimators':-1}})
        assert code==400 and 'n_estimators' in error['error']
        code,error=fetch('/api/v2/training/start',{'channel_ids':[1], 'algorithm':'not_registered',
             'algorithm_params':{}})
        assert code==400 and '未知检测算法' in error['error']
        code,error=fetch('/api/v2/training/start',{'channel_ids':[1], 'algorithm':'zscore','table':'old_table'})
        assert code==400 and '旧数据表' in error['error']
    finally:
        server.shutdown();worker.join(timeout=5);server.server_close()
