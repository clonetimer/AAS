"""Score-only V2 CSV tests: truthful unknown labels and exact saved model layout."""
from __future__ import annotations

import sqlite3
import json
import gzip
from types import SimpleNamespace

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.feature_schema import build_feature_schema
from scripts.server.telemetry_v2_source_import import source_operation
from scripts.server.v2_run_dataset import catalog, load_selection


KEYS = ['ADCS.RW1.temp', 'ADCS.RW1.speed']
SEMANTICS = [['ADCS', 'RW1', 'temp', 'C'], ['ADCS', 'RW1', 'speed', 'rad/s']]


def make_db(tmp_path):
    path = tmp_path / 'aas_telemetry_v2.sqlite'
    with sqlite3.connect(path) as db:
        create_v2(db)
    return path


def csv_config(*, namespace='test-source', role='test', unit='rad/s'):
    return {'operation': 'commit', 'filename': 'telemetry.csv', 'csv_encoding':'utf-8',
            'csv_delimiter':',', 'format_confirmed':True, 'config': {
        'namespace': namespace, 'model': 'USER-SAT', 'subsystem': 'ADCS',
        'device': 'RW1', 'variable': 'wide', 'unit': unit, 'role': role,
        'time_domain': 'numeric_unspecified', 'time_reference': 'run-clock-99', 'time_unit': 's',
        'unlabeled_test_confirmed': role == 'test', 'all_normal_confirmed': role == 'train',
        'bind_digest_group': True,
        'selected_columns': ['speed', 'temp'],
        'column_metadata': {'speed': {'subsystem': 'ADCS', 'device': 'RW1',
                                      'variable': 'speed', 'unit': unit},
                            'temp': {'subsystem': 'ADCS', 'device': 'RW1',
                                     'variable': 'temp', 'unit': 'C'}},
    }}


def import_unknown(db, *, namespace='test-source'):
    raw = b'time_s,speed,temp\n0,10,30\n1,11,31\n2,12,32\n3,13,33\n'
    return source_operation(db, raw, csv_config(namespace=namespace))


def selection(source_id):
    return {'source_ids': [source_id], 'feature_keys': list(KEYS),
            'start': None, 'end': None, 'method': 'exact', 'tolerance_seconds': 0}


def expectations(*, schema=None, **overrides):
    return {'feature_keys': list(KEYS), 'feature_schema': schema or build_feature_schema(KEYS, SEMANTICS),
            'train_source_ids': [], 'train_groups': [], 'train_source_digests': [], **overrides}


def test_unlabeled_test_import_is_only_score_only_not_supervised(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db = make_db(tmp_path)
    record = import_unknown(db)
    discovered = next(run for run in catalog(db)['runs'] if run['source_id'] == record['source_id'])
    assert discovered['allowed_role'] == 'score_only'
    assert discovered['unknown_frames'] == 4  # Two channels, four frames (not eight cells).
    with pytest.raises(ValueError):
        load_selection(db, selection=selection(record['source_id']), role='test')
    rows, info = load_score_only_rows(db, selection=selection(record['source_id']), expected=expectations())
    assert info['ground_truth_available'] is False
    assert info['aligned_count'] == 4
    assert [(x['time_s'], x['value'], x['source_2']) for x in rows] == [
        (0.0, 30.0, 10.0), (1.0, 31.0, 11.0),
        (2.0, 32.0, 12.0), (3.0, 33.0, 13.0)]
    assert all('label' not in x for x in rows)


def test_score_only_rejects_same_training_group_or_digest(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db = make_db(tmp_path)
    record = import_unknown(db)
    sid = record['source_id']
    rows, contract = load_score_only_rows(db, selection=selection(sid), expected=expectations())
    assert len(rows) == 4
    with pytest.raises(ValueError, match='训练|泄漏'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations(train_source_ids=[sid]))
    with pytest.raises(ValueError, match='训练|泄漏'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations(
            train_groups=[contract['runs'][0]['group_id']]))
    with pytest.raises(ValueError, match='训练|泄漏'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations(
            train_source_digests=[contract['runs'][0]['source_sha256']]))


def test_score_only_rejects_training_samples_with_label_column_removed(tmp_path):
    """Raw file SHA and digest-bound group must not certify disjoint telemetry."""
    from scripts.server.v2_score_only import load_score_only_rows
    db=make_db(tmp_path)
    data=[(frame,10+frame,30+frame) for frame in range(16)]
    train_raw=('time_s,speed,temp,label\n'+''.join(
        f'{frame},{speed},{temp},0\n' for frame,speed,temp in data)).encode()
    unknown_raw=('time_s,speed,temp\n'+''.join(
        f'{frame},{speed},{temp}\n' for frame,speed,temp in data)).encode()
    train=source_operation(db,train_raw,csv_config(namespace='train-with-label',role='train'))
    test=source_operation(db,unknown_raw,csv_config(namespace='test-without-label'))
    runs={r['source_id']:r for r in catalog(db)['runs']}
    assert runs[train['source_id']]['group_id']!=runs[test['source_id']]['group_id']
    with sqlite3.connect(db) as conn:
        digests=dict(conn.execute('SELECT id,source_sha256 FROM v2_sources'))
    assert digests[train['source_id']]!=digests[test['source_id']]
    with pytest.raises(ValueError,match='重复|重叠|泄漏'):
        load_score_only_rows(db,selection=selection(test['source_id']),
            expected=expectations(train_source_ids=[train['source_id']],
                train_groups=[runs[train['source_id']]['group_id']],
                train_source_digests=[digests[train['source_id']]]))


def test_score_only_accepts_distinct_telemetry_from_same_clock(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db=make_db(tmp_path)
    train_raw=b'time_s,speed,temp,label\n0,100,300,0\n1,101,301,0\n2,102,302,0\n3,103,303,0\n'
    train=source_operation(db,train_raw,csv_config(namespace='other-train',role='train'))
    test=import_unknown(db,namespace='independent-test')
    with sqlite3.connect(db) as conn:
        digest=conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?',
            (train['source_id'],)).fetchone()[0]
    rows,info=load_score_only_rows(db,selection=selection(test['source_id']),
        expected=expectations(train_source_ids=[train['source_id']],
            train_source_digests=[digest]))
    assert len(rows)==4 and info['ground_truth_available'] is False


def test_score_only_requires_verifiable_saved_training_source(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db=make_db(tmp_path)
    sid=import_unknown(db)['source_id']
    with pytest.raises(ValueError,match='训练来源|独立性'):
        load_score_only_rows(db,selection=selection(sid),
            expected=expectations(train_source_ids=[9999],
                                  train_source_digests=['f'*64]))


def test_unknown_test_coverage_does_not_require_labels(tmp_path):
    from scripts.server.v2_run_dataset import selection_coverage
    db=make_db(tmp_path)
    sid=import_unknown(db)['source_id']
    coverage=selection_coverage(db,role='score_only',source_ids=[sid],feature_keys=KEYS)
    assert coverage['has_common_window'] is True
    assert coverage['start_s']==0 and coverage['end_s']==3
    with pytest.raises(ValueError):
        selection_coverage(db,role='test',source_ids=[sid],feature_keys=KEYS)


def test_feature_catalog_preserves_units_by_selected_csv_source(tmp_path):
    db=make_db(tmp_path)
    sid_radians=import_unknown(db,namespace='radians')['source_id']
    sid_degrees=source_operation(db,b'time_s,speed,temp\n0,1,21\n1,2,22\n',
        csv_config(namespace='degrees',unit='deg/s'))['source_id']
    discovered=catalog(db)
    # Global unique display keys are only a picker index, not a source attestation.
    assert next(item for item in discovered['run_features'][str(sid_radians)]
                if item['feature_key']=='ADCS.RW1.speed')['unit']=='rad/s'
    assert next(item for item in discovered['run_features'][str(sid_degrees)]
                if item['feature_key']=='ADCS.RW1.speed')['unit']=='deg/s'


def test_score_only_matches_model_feature_alias_by_attested_semantics(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    from scripts.server.v2_run_dataset import selection_coverage
    db=make_db(tmp_path)
    sid=import_unknown(db)['source_id']
    model_keys=['legacy.rw_temperature','legacy.rw_speed']
    model_selection={**selection(sid),'feature_keys':model_keys}
    expected=expectations(feature_keys=model_keys,
                          feature_schema=build_feature_schema(model_keys,SEMANTICS))
    rows,info=load_score_only_rows(db,selection=model_selection,expected=expected)
    assert len(rows)==4 and info['feature_keys']==model_keys
    coverage=selection_coverage(db,role='score_only',source_ids=[sid],
                    feature_keys=model_keys,feature_semantics=SEMANTICS)
    assert coverage['has_common_window'] and coverage['start_s']==0
    invalid=[['ADCS','RW1','temp','K'],SEMANTICS[1]]
    with pytest.raises(ValueError,match='参量|单位|物理|缺少'):
        load_score_only_rows(db,selection=model_selection,
            expected=expectations(feature_keys=model_keys,
                feature_schema=build_feature_schema(model_keys,invalid)))


def test_score_only_rejects_changed_physical_semantics(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db = make_db(tmp_path)
    sid = import_unknown(db)['source_id']
    wrong = build_feature_schema(KEYS, [['ADCS', 'RW1', 'temp', 'K'], SEMANTICS[1]])
    with pytest.raises(ValueError, match='特征|物理|单位'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations(schema=wrong))
    with pytest.raises(ValueError, match='顺序|特征'):
        load_score_only_rows(db, selection={**selection(sid), 'feature_keys': KEYS[::-1]},
                             expected=expectations())


def test_score_only_rejects_mixed_labels_and_duplicate_time(tmp_path):
    from scripts.server.v2_score_only import load_score_only_rows
    db = make_db(tmp_path)
    sid = import_unknown(db)['source_id']
    with sqlite3.connect(db) as conn:
        conn.execute('''UPDATE v2_samples SET label_int=0 WHERE id IN
            (SELECT s.id FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
             WHERE g.source_id=? ORDER BY s.id LIMIT 1)''', (sid,))
    with pytest.raises(ValueError, match='未知|标签|仅推理'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations())
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE v2_samples SET label_int=NULL WHERE label_int=0')
        conn.execute('''UPDATE v2_samples SET time_sort_num=0 WHERE id IN
            (SELECT s.id FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
             WHERE g.source_id=? AND s.time_sort_num=1 ORDER BY s.id LIMIT 1)''',(sid,))
    with pytest.raises(ValueError, match='时间|重复'):
        load_score_only_rows(db, selection=selection(sid), expected=expectations())


class NoRefitDetector:
    """Real, deterministic legacy detector behavior; fit must never run."""
    def __init__(self, *, bad_scores=False, bad_threshold=False):
        self.fit_calls = 0
        self.seen = None
        self.bad_scores = bad_scores
        self.bad_threshold = bad_threshold

    def fit(self, values):
        self.fit_calls += 1
        raise AssertionError('A saved detector must never be trained on test data')

    def score(self, values):
        return [float(row[0]) for row in values]

    def detect(self, values):
        self.seen = values.tolist()
        scores = self.score(values)
        if self.bad_scores:
            scores = scores[:-1]
        return {'anomaly_score':scores,'pred_label':[int(x[0]>0.5) for x in values],
                'threshold':float('nan') if self.bad_threshold else 0.5,
                'decision_mode':'score_threshold'}


def scaler():
    return {'enabled':True, 'method':'min_max', 'parameters':{
        'method':'min_max','per_feature':{
            'value':{'method':'min_max','min':30.0,'max':33.0},
            'source_2':{'method':'min_max','min':10.0,'max':13.0}}}}


def test_score_only_runs_preloaded_detector_with_frozen_scaling_and_no_metrics(tmp_path):
    from scripts.server.v2_score_only import score_with_published_detector
    db=make_db(tmp_path)
    sid=import_unknown(db)['source_id']
    detector=NoRefitDetector()
    result=score_with_published_detector(db,selection=selection(sid),expected=expectations(),
        scaling=scaler(),feature_columns=['value','source_2'],
        algorithm='zscore',detector=detector)
    assert detector.fit_calls==0
    assert detector.seen==[[0.0,0.0],[1/3,1/3],[2/3,2/3],[1.0,1.0]]
    detail=result['result']
    assert result['evaluation_mode']=='score_only'
    assert detail['metrics']=={}
    assert detail['ground_truth_available'] is False
    assert detail['pred_labels']==[0,0,1,1]
    assert detail['threshold']==[0.0,1/3,2/3,1.0]
    assert len(detail['rows'])==4 and all('label' not in row for row in detail['rows'])


def test_score_only_detects_invalid_score_vector_and_scaler_contract(tmp_path):
    from scripts.server.v2_score_only import score_with_published_detector
    db=make_db(tmp_path)
    sid=import_unknown(db)['source_id']
    kwargs=dict(selection=selection(sid),expected=expectations(),
                scaling=scaler(),feature_columns=['value','source_2'],algorithm='zscore')
    with pytest.raises(ValueError, match='length|数量|分数'):
        score_with_published_detector(db,detector=NoRefitDetector(bad_scores=True),**kwargs)
    wrong=scaler();wrong['parameters']['per_feature']['value']['max']=9.0
    # Wrong scaler can be a genuine historic training range: don't infer from
    # test data. Instead reject a missing/misordered persisted scaler contract.
    wrong['parameters']['per_feature']={'source_2':wrong['parameters']['per_feature']['source_2'],
                                        'value':wrong['parameters']['per_feature']['value']}
    with pytest.raises(ValueError,match='归一化|特征'):
        score_with_published_detector(db,detector=NoRefitDetector(),**{**kwargs,'scaling':wrong})
    with pytest.raises(ValueError,match='阈值|NaN|Inf'):
        score_with_published_detector(db,detector=NoRefitDetector(bad_threshold=True),**kwargs)


def test_real_isolated_worker_trains_then_scores_unlabeled_csv(tmp_path,monkeypatch):
    """No fake detector/supervisor: exercise the real model and child process."""
    from scripts.server.algorithm_process import AlgorithmProcessService
    from scripts.server.v2_run_dataset import validate_training_selection
    from scripts.core.model_manager import load_model_config
    folder=tmp_path/'datasets';folder.mkdir()
    db=make_db(folder)
    train_csv=b'time_s,speed,temp\n'+b''.join(
        f'{i},{10+i/20},{30+i/20}\n'.encode() for i in range(16))
    train=source_operation(db,train_csv,csv_config(namespace='training-only',role='train'))
    test=import_unknown(db,namespace='heldout-unknown')
    train_sel=selection(train['source_id'])
    preview=validate_training_selection(db,{'dataset_selection':train_sel,'train_ratio':0.75})
    train_sel['feature_keys']=preview['feature_keys']
    monkeypatch.setenv('AAS_V2_DB_FILENAME',db.name)
    service=AlgorithmProcessService(tmp_path,tmp_path/'models')
    trained=service.run_training(algorithm='three_sigma',algorithm_params={},
        selection=train_sel,train_ratio=0.75,normalization_method='min_max',
        training_strategy='standard',expected={'feature_keys':preview['feature_keys'],
        'train_run_evidence':preview['runs'],'feature_schema':preview['feature_schema']},
        database=db.name)
    saved=load_model_config(trained['model_info']['model_dir'])['config']['data_contract']
    model_selection={**selection(test['source_id']),
                     'feature_keys':saved['v2_run_selection']['feature_keys']}
    result=service.run_score_only(algorithm='three_sigma',selection=model_selection,
        expected={'feature_keys':model_selection['feature_keys'],
                  'train_source_ids':saved['v2_run_selection']['source_ids'],
                  'train_groups':[run['group_id'] for run in saved['v2_run_manifest']['runs']],
                  'train_source_digests':[run.get('source_sha256') for run in saved['v2_run_manifest']['runs']],
                  'feature_schema':saved['feature_schema']},
        scaling=saved['model_input_scaling'],feature_columns=saved['feature_columns'],
        model_dir=trained['model_info']['model_dir'],database=db.name)
    assert result['evaluation_mode']=='score_only'
    assert result['ground_truth_available'] is False
    assert result['result']['metrics']=={}
    assert len(result['result']['rows'])==4
    assert all('label' not in row for row in result['result']['rows'])
    assert not list(service.job_dir.iterdir())


def test_score_only_route_uses_published_model_and_archives_honest_report(tmp_path):
    from scripts.server.telemetry_v2_routes import _inference
    from scripts.server.execution_provenance import ExecutionRegistry
    from scripts.server.feature_schema import attest_feature_schema
    db=tmp_path/'datasets'/'aas_telemetry_v2.sqlite'
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        create_v2(conn)
    sid=import_unknown(db)['source_id']
    train=source_operation(db,b'time_s,speed,temp,label\n0,100,300,0\n1,101,301,0\n',
        csv_config(namespace='route-train',role='train'))
    with sqlite3.connect(db) as conn:
        train_digest=conn.execute('SELECT source_sha256 FROM v2_sources WHERE id=?',
                                  (train['source_id'],)).fetchone()[0]
    model_dir=tmp_path/'models'/'model_example'
    model_dir.mkdir(parents=True)
    dc={'version':7,'v2_run_selection':{'source_ids':[train['source_id']], 'feature_keys':KEYS,
                    'method':'exact','tolerance_seconds':0},
        'v2_run_manifest':{'runs':[{'source_id':train['source_id'],
                                     'group_id':'other-train','source_sha256':train_digest}]},
        'model_input_scaling':scaler(),'feature_columns':['value','source_2']}
    attest_feature_schema(dc,keys=KEYS,semantics=SEMANTICS)
    model={'id':5,'model_name':'registered','algorithm':'zscore','file_path':'models/model_example',
           'status':'published'}
    class FakeSupervisor:
        def run_score_only(self,**kwargs):
            assert kwargs['model_dir']==model_dir
            return score_with_published_detector(db,selection=kwargs['selection'],
                 expected=kwargs['expected'],scaling=kwargs['scaling'],
                 feature_columns=kwargs['feature_columns'],
                 algorithm=kwargs['algorithm'],detector=NoRefitDetector())
    from scripts.server.v2_score_only import score_with_published_detector
    app=SimpleNamespace(ROOT=tmp_path, get_model=lambda mid:model if mid==5 else None,
        resolve_managed_model_dir=lambda ref:model_dir,
        load_model_config=lambda directory:{'config':{'data_contract':dc}},
        ALGORITHM_SUPERVISOR=FakeSupervisor(),EXECUTION_REGISTRY=ExecutionRegistry())
    response=_inference(app,json.dumps({'model_id':5,'dataset_selection':selection(sid)}).encode())
    assert response['evaluation_mode']=='score_only'
    assert response['metrics']=={} and response['ground_truth_available'] is False
    assert response['archive_status']=='complete'
    report=tmp_path/response['summary_path']
    content=json.loads(report.read_text('utf-8'))
    assert content['results'][0]['metrics']=={}
    assert content['experiment']['type']=='evaluation'
    assert content['evaluation']['evaluation_mode']=='score_only'
    assert content['evaluation']['ground_truth_available'] is False
    provenance=content['provenance']
    assert provenance['input_before']['status']=='unavailable'
    assert provenance['input_after']['status']=='unavailable'
    assert provenance['input_changed_during_execution'] is None
    assert provenance['effective_model_inputs']['snapshot_status']=='complete'
    snapshot=tmp_path/response['summary_path'].replace('summary.json','execution_inputs.json.gz')
    from scripts.core.input_evidence import verify_input_snapshot
    with gzip.open(snapshot,'rt',encoding='utf-8') as stream:
        assert verify_input_snapshot(json.load(stream),provenance['effective_model_inputs'])
    from scripts.server.legacy_catalog_api import list_experiment_summaries
    records=list_experiment_summaries(SimpleNamespace(ROOT=tmp_path,
        read_json=lambda path:json.loads(path.read_text('utf-8'))),'evaluation')
    assert len(records)==1
    assert records[0]['evaluation_mode']=='score_only'
    assert records[0]['ground_truth_available'] is False
    from scripts.server.telemetry_v2_routes import _inference_preview
    preview=_inference_preview(app,json.dumps({'model_id':5,'dataset_selection':selection(sid)}).encode())
    assert preview['evaluation_mode']=='score_only'
    assert preview['plot_series'][0]['plotted_count']==4
    assert all(point['label'] is None for point in preview['plot_series'][0]['points'])
    from scripts.server.task_diagnostics import TaskOperationError
    with pytest.raises(TaskOperationError,match='模型'):
        _inference(app,json.dumps({'model_id':999,'dataset_selection':selection(sid)}).encode())
    with pytest.raises(TaskOperationError):
        _inference(app,json.dumps({'model_id':5,'dataset_selection':selection(sid),
                                   'metrics':{'accuracy':1}}).encode())
