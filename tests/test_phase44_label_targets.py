"""Phase 44 regression: target provenance, unknown exclusion, no OR-label leak."""
import sqlite3
from pathlib import Path
import pytest
from tests.test_v2_run_subset_phase8 import labeled, choose
from scripts.server.v2_run_dataset import load_selection
from scripts.server.v2_label_targets import normalize_target
from scripts.server.telemetry_v2_governance import create_event


def locations(db, *, subsystem='adcs', device='reaction_wheel'):
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE v2_run_fault_locations (
            source_id INTEGER PRIMARY KEY,subsystem TEXT NOT NULL,device TEXT NOT NULL,
            classification_basis TEXT NOT NULL,note TEXT NOT NULL)''')
        conn.execute('INSERT INTO v2_run_fault_locations VALUES (?,?,?,?,?)',
                     (4,subsystem,device,'expert_verified','source attribution'))


def test_run_truth_still_available_and_not_inferred_component(labeled):
    rows,meta=load_selection(labeled,selection=choose([3,4]),role='test')
    assert len(rows)==12 and meta['fault_count']==2
    assert meta['label_target']=={'scope':'run'}
    assert meta['label_coverage']['excluded_unknown']==0
    with pytest.raises(ValueError,match='位置凭据'):
        load_selection(labeled,selection=choose([3,4]),role='test',
            label_target={'scope':'component','subsystem':'adcs','device':'reaction_wheel'})


def test_component_truth_only_for_exact_location(labeled):
    locations(labeled)
    rows,meta=load_selection(labeled,selection=choose([3,4]),role='test',
        label_target={'scope':'component','subsystem':'adcs','device':'reaction_wheel'})
    assert len(rows)==12 and meta['fault_count']==2
    assert meta['runs'][1]['label_evidence']['fault_source_match'] is True
    assert {r['label'] for r in rows if r['run_source_id']==4 and r['time_s']>=4}=={1}


def test_other_component_fault_never_becomes_false_rw_fault_or_normal(labeled):
    locations(labeled,subsystem='adcs',device='star_tracker')
    with pytest.raises(ValueError,match='明确的正常和故障'):
        load_selection(labeled,selection=choose([3,4]),role='test',
            label_target={'scope':'component','subsystem':'adcs','device':'reaction_wheel'})
    # Add a matching fault run as a third verification source, and ensure
    # uncertain labels of source 4 remain excluded instead of being treated as 0.
    with sqlite3.connect(labeled) as conn:
        conn.execute("UPDATE v2_run_annotations SET split='validation' WHERE source_id=5")
        conn.execute('INSERT INTO v2_run_fault_locations VALUES (?,?,?,?,?)',
                     (5,'adcs','reaction_wheel','expert_verified','second source'))
    rows,meta=load_selection(labeled,selection=choose([3,4,5]),role='test',
        label_target={'scope':'component','subsystem':'adcs','device':'reaction_wheel'})
    assert meta['label_coverage']['excluded_unknown']==2
    assert len(rows)==16 and meta['fault_count']==2
    assert all(r['label']==0 for r in rows if r['run_source_id']==4)
    assert all('label_block_id' in r for r in rows)


def test_parameter_requires_explicit_event_truth_both_classes(labeled):
    target={'scope':'parameter','feature_key':'adcs.rw.speed_rad_s'}
    with pytest.raises(ValueError,match='已确认标签'):
        load_selection(labeled,selection=choose([3,4]),role='test',label_target=target)
    create_event(labeled,{'channel_id':7,'role':'simulation','start':'0','end':'4',
                'label':0,'confirm':True})
    create_event(labeled,{'channel_id':7,'role':'simulation','start':'4','end':'6',
                'label':1,'fault_type':'rw_observation','confirm':True})
    rows,meta=load_selection(labeled,selection=choose([3,4]),role='test',label_target=target)
    assert len(rows)==6 and meta['fault_count']==2 and meta['normal_count']==4
    assert meta['label_coverage']=={'aligned_before_label_filter':12,'known':6,'excluded_unknown':6}
    assert {r['run_source_id'] for r in rows}=={4}
    assert meta['runs'][1]['label_evidence']['source']=='v2_fault_events'
    assert all(r['label']==(r['time_s']>=4) for r in rows)


def test_target_validation_and_no_hidden_scope_conversion(labeled):
    for payload in [{'scope':'component','subsystem':'adcs'},
                    {'scope':'parameter','feature_key':'typo'},
                    {'scope':'run','feature_key':'x'}, {'scope':'bad'}]:
        with pytest.raises(ValueError):
            load_selection(labeled,selection=choose([3,4]),role='test',label_target=payload)
    with pytest.raises(ValueError,match='正常训练'):
        load_selection(labeled,selection=choose([1,2]),role='train',
            label_target={'scope':'parameter','feature_key':'adcs.rw.speed_rad_s'})


def test_ui_and_worker_contract_present():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/runner.html').read_text('utf-8')
    js=(root/'web/visualization/runner-v2.js').read_text('utf-8')
    assert 'id="evalLabelScope"' in html and 'id="evalLabelEntity"' in html
    assert 'function evaluationTarget()' in js and 'label_target:evaluationSnapshot.label_target' in js
    assert 'v2_label_target=payload.get' in (root/'scripts/server/algorithm_worker.py').read_text('utf-8')


def test_actual_worker_scores_component_target_without_refit(tmp_path,monkeypatch):
    from scripts.server.algorithm_process import AlgorithmProcessService
    from scripts.server.v2_run_dataset import validate_training_selection
    from scripts.core.model_manager import load_model_config
    data=tmp_path/'datasets';data.mkdir()
    db=labeled.__wrapped__(data)
    locations(db)
    monkeypatch.setenv('AAS_V2_DB_FILENAME',db.name)
    svc=AlgorithmProcessService(tmp_path,tmp_path/'models')
    training_sel=choose([1,2]);preview=validate_training_selection(db,{
        'dataset_selection':training_sel,'train_ratio':.75})
    train=svc.run_training(algorithm='three_sigma',algorithm_params={},selection=training_sel,
        train_ratio=.75,normalization_method='zscore',training_strategy='standard',
        expected={'feature_keys':preview['feature_keys'],'train_run_evidence':preview['runs']},
        database=db.name)
    path=Path(train['model_info']['model_dir']);dc=load_model_config(path)['config']['data_contract']
    expected={'feature_keys':training_sel['feature_keys'],'train_source_ids':[1,2],
              'train_groups':[r['group_id'] for r in dc['v2_run_manifest']['runs']]}
    target={'scope':'component','subsystem':'adcs','device':'reaction_wheel'}
    evaluation=svc.run_evaluation(algorithm='three_sigma',selection=choose([3,4]),expected=expected,
        scaling=dc['model_input_scaling'],feature_columns=dc['feature_columns'],model_dir=path,
        database=db.name,label_target=target)
    assert evaluation['status']=='success'
    assert evaluation['config']['data_contract']['v2_label_target']==target
    assert len(evaluation['result']['pred_labels'])==12
    assert sum(row['label'] for row in evaluation['result']['rows'])==2
    assert len(list(svc.models_dir.glob('model_*')))==1
    assert not list(svc.job_dir.iterdir())


def test_parameter_events_must_not_leak_across_shared_channel(labeled):
    target={'scope':'parameter','feature_key':'adcs.rw.speed_rad_s'}
    # Another run shares the same logical channel id while retaining its own
    # samples; events on that channel cannot distinguish its source ownership.
    with sqlite3.connect(labeled) as conn:
        conn.execute('UPDATE v2_segments SET channel_id=7 WHERE id=9')
    with pytest.raises(ValueError,match='唯一来源运行'):
        load_selection(labeled,selection=choose([3,4]),role='test',label_target=target)


def test_multichannel_mismatched_labels_never_or_merged(tmp_path):
    from scripts.rebuild_telemetry_v2 import create_v2
    from scripts.server.telemetry_v2_import import import_csv
    from scripts.server.telemetry_v2_training import load_matrix
    path=tmp_path/'multi.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    ids=[]
    for variable,labels in [('speed',[0,0,1,1]),('current',[0,0,0,0])]:
        cfg={'namespace':'same_run','model':'M','subsystem':'adcs','device':'reaction_wheel',
             'variable':variable,'unit':'rad/s','role':'test','filename':variable+'.csv',
             'time_domain':'numeric_unspecified','time_reference':'same_clock','time_unit':'s',
             'csv':'timestamp,value,label\n'+''.join(f'{i},{10+i},{label}\n' for i,label in enumerate(labels))}
        ids.append(import_csv(path,cfg,commit=True)['channel_id'])
    with pytest.raises(ValueError,match='禁止以 max/OR'):
        load_matrix(path,channel_ids=ids,role='test')
