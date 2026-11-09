"""Read-only telemetry catalogue metadata and automatic evaluation-domain regression."""
from pathlib import Path
import sqlite3

from scripts.server.evaluation_domain import classify_evaluation_mapping
from scripts.server.services import sqlite_tables

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {'model':'DEMO','subsystem':'ADCS','device':'demo_unit','variable':'temperature'}


def _table(name, variable='temperature'):
    return {'name':name, 'hierarchy':[{**IDENTITY, 'variable':variable}]}


def test_cross_parameter_is_inferred_without_user_switch():
    sources=[{'table':'train_temp','identity':IDENTITY}]
    result=classify_evaluation_mapping(sources,['test_current'],
        {'test_current':_table('test_current','current')})
    assert result['evaluation_mode']=='cross_domain'
    assert result['evaluation_mapping']=={'train_temp':'test_current'}
    assert result['domain_differences'][0]['different_fields']==['variable']


def test_matching_and_unknown_identity_distinguished():
    sources=[{'table':'train_temp','identity':IDENTITY}]
    same=classify_evaluation_mapping(sources,['test_temp'], {'test_temp':_table('test_temp')})
    assert same['evaluation_mode']=='same_domain'
    unknown=classify_evaluation_mapping([{'table':'old','identity':None}],
       ['test_temp'], {'test_temp':_table('test_temp')})
    assert unknown['evaluation_mode']=='unverified_domain'
    assert unknown['domain_differences'][0]['different_fields'] is None


def test_multiple_feature_slots_record_exact_order():
    src=[{'table':'A','identity':IDENTITY},
         {'table':'B','identity':{**IDENTITY,'variable':'vibration'}}]
    result=classify_evaluation_mapping(src,['t_vibration','t_temp'],
        {'t_vibration':_table('t_vibration','vibration'),
         't_temp':_table('t_temp')})
    assert result['evaluation_mode']=='cross_domain'
    assert [x['position'] for x in result['domain_differences']]==[1,2]
    assert result['evaluation_mapping']=={'A':'t_vibration','B':'t_temp'}


def test_catalog_exposes_real_time_coverage_and_rows():
    rows={item['name']:item for item in sqlite_tables(ROOT/'datasets/history_sample.db')}
    example=rows['DEMO_train_temperature']
    assert example['time_start']=='2026-01-01 00:00:00'
    assert example['time_end']=='2026-01-01 01:19:50'
    assert example['row_count']==480
    assert example['is_single_measurement']


def test_legacy_telemetry_without_timestamp_returns_unknown_bounds(tmp_path):
    path=tmp_path/'untimed.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE legacy(model TEXT, subsystem TEXT,device TEXT,variable TEXT,value REAL)')
        conn.execute("INSERT INTO legacy VALUES ('M','S','D','V',1)")
    rows=sqlite_tables(path)
    assert len(rows)==1
    assert rows[0]['time_start'] is None and rows[0]['time_end'] is None


def test_evaluation_ui_infers_and_persists_domain_without_opt_in():
    root=ROOT/'web'/'visualization'
    runner=(root/'runner.js').read_text(encoding='utf-8')
    markup=(root/'runner.html').read_text(encoding='utf-8')
    assert 'crossDomainEvaluation' not in runner
    assert 'crossDomainEvaluation' not in markup
    assert 'domain_differences: inputContract.domain_differences || []' in runner
    assert 'evaluation_mode: detectedDomain' in runner
    assert 'showEvaluationDomainDifferences()' in runner
