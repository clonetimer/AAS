"""Phase 50 gate isolation and data-safe performance smoke tests."""
import gzip
import json
import sqlite3
import zipfile

import pytest

from scripts.check_v2_stable import _partition_tests, _write_gate_report
from scripts.pressure_v2_batch_import import benchmark


def fixture(tmp_path,*,role='FAULT',split='validation'):
    token='a'*24
    group='b'*24
    archive=tmp_path/'targets.zip'
    target={'token':token,'split':split,'group':group,'role':role,
            'fault':'adcs.rw_friction' if role=='FAULT' else None,
            'component':'adcs.rw' if role=='FAULT' else None,
            'onset_s':5 if role=='FAULT' else None,
            'metadata_not_model_features':True}
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(f'targets/{token}.json',json.dumps(target))
    path=tmp_path/(token+'.csv.gz')
    text='time_s,eps.battery_energy_j,eps.bus_load_power_w\n' + ''.join(
        f'{i},{100+i},{5+i%2}\n' for i in range(10))
    with gzip.open(path,'wt',encoding='utf-8') as f:f.write(text)
    return path,archive


def test_partition_is_complete_ordered_bounded_and_process_isolated():
    nodes=[f'tests/test_one.py::test_{i}' for i in range(23)]
    nodes += [f'tests/test_phase41_algorithm_process.py::test_{i}' for i in range(4)]
    nodes += [f'tests/test_two.py::test_{i}' for i in range(12)]
    batches=_partition_tests(nodes,10)
    assert [x for group in batches for x in group]==nodes
    assert all(0<len(group)<=10 for group in batches)
    assert all(not(any('test_phase41_algorithm_process' in n for n in group)
                   and any('test_phase41_algorithm_process' not in n for n in group)) for group in batches)


def test_gate_report_writes_atomic_valid_json(tmp_path):
    path=tmp_path/'report.json'
    _write_gate_report(path,{'status':'running','batches':[]})
    _write_gate_report(path,{'status':'passed','batches':[{'exit_code':0}]})
    assert json.loads(path.read_text(encoding='utf-8'))['status']=='passed'
    assert list(tmp_path.glob('.*'))==[]


def test_pressure_preview_commit_receipt_and_label_isolation(tmp_path):
    path,targets=fixture(tmp_path)
    original=path.read_bytes()
    report=benchmark(path,targets,model='SATELLITE-FAMILY')
    assert report['status']=='passed'
    assert report['counts']=={'v2_sources':1,'v2_segments':2,'v2_samples':20,
                              'v2_batch_import_receipts':1}
    assert report['actual_sample_labels']=={'unknown':20}
    assert report['dataset_split']=='validation'
    assert report['run_role']=='FAULT'
    assert report['measurements']['commit_seconds']>=0
    assert path.read_bytes()==original
    assert list(tmp_path.glob('isolated-v2.sqlite'))==[]


def test_pressure_normal_requires_explicit_confirmation(tmp_path):
    path,targets=fixture(tmp_path,role='NORMAL',split='train')
    with pytest.raises(ValueError,match='人工确认'):
        benchmark(path,targets,model='SATELLITE-FAMILY')
    report=benchmark(path,targets,model='SATELLITE-FAMILY',confirmed_normal=True)
    assert report['status']=='passed'
    assert report['actual_sample_labels']=={'0':20}


def test_pressure_does_not_guess_unknown_column_unit(tmp_path):
    path,targets=fixture(tmp_path)
    with gzip.open(path,'wt',encoding='utf-8') as f:
        f.write('time_s,propulsion.burn_commanded\n0,1\n1,0\n')
    with pytest.raises(ValueError,match='unit 必须是'):
        benchmark(path,targets,model='SATELLITE-FAMILY')
    report=benchmark(path,targets,model='SATELLITE-FAMILY',
                     unit_overrides={'propulsion.burn_commanded':'1'})
    assert report['status']=='passed' and report['counts']['v2_samples']==2


def test_capacity_guard_prevents_partial_database_write(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts.rebuild_telemetry_v2 import create_v2
    from scripts.server.telemetry_v2_batch_import import import_file
    from scripts.build_v2_batch_manifest import build_plan
    import scripts.server.telemetry_v2_batch_import as batch
    path,targets=fixture(tmp_path)
    plan,_=build_plan(tmp_path,targets,model='SATELLITE-FAMILY')
    config=plan['files'][0]['config']
    db=tmp_path/'empty.sqlite'
    with sqlite3.connect(db) as conn:create_v2(conn)
    monkeypatch.setattr(batch.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
    with pytest.raises(ValueError,match='可用空间不足'):
        import_file(db,path,config)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='v2_batch_import_receipts'").fetchone()[0]==0


def test_preview_reports_storage_amplification_risk(tmp_path):
    from scripts.rebuild_telemetry_v2 import create_v2
    from scripts.server.telemetry_v2_batch_import import import_file
    from scripts.build_v2_batch_manifest import build_plan
    path,targets=fixture(tmp_path)
    plan,_=build_plan(tmp_path,targets,model='SATELLITE-FAMILY')
    db=tmp_path/'empty.sqlite'
    with sqlite3.connect(db) as conn:create_v2(conn)
    result=import_file(db,path,plan['files'][0]['config'],dry_run=True)
    quality=result['quality']
    assert quality['estimated_sqlite_growth_bytes']>quality['raw_row_json_utf8_bytes']
    assert quality['storage_estimate_kind'].startswith('conservative_')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==0
