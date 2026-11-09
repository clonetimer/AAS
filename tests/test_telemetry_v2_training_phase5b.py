"""Phase 5B: V2 channel matrix, input contract and retirement safety."""
from pathlib import Path
import hashlib
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_training import load_matrix, validate_training_selection
from scripts.retire_legacy_databases import retire


def build(tmp_path, *, domain='datetime_utc'):
    path=tmp_path/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    for role in ('train','test'):
        for variable,unit,mul in (('speed','rpm',1),('current','A',10)):
            def stamp(i):return (f'2026-10-01T00:00:{i:02d}Z' if domain=='datetime_utc' else str(i))
            def part(start,stop):return 'timestamp,value,label\n'+''.join(
                f'{stamp(i)},{i*mul},{int(role=="test" and i>=10)}\n'
                for i in range(start,stop))
            for batch, (low,high) in enumerate([(0,9),(9,20)]):
                import_csv(path,dict(namespace='mission_A',satellite_instance='SAT1',model='GF1',
                   subsystem='ADCS',device='RW1',variable=variable,unit=unit,role=role,
                   time_domain=domain,time_reference='mission_A_clock' if domain!='datetime_utc' else None,
                   time_unit='s' if domain!='datetime_utc' else None,
                   filename=f'{role}_{variable}_{batch}.csv',csv=part(low,high)),commit=True)
    return path


def test_cross_partition_multichannel_training_and_scoring(tmp_path):
    path=build(tmp_path)
    train,contract=load_matrix(path,channel_ids=[1,2],role='train')
    test,_=load_matrix(path,channel_ids=[1,2],role='test',expected=contract['sources'])
    assert len(train)==len(test)==20
    assert {r['label'] for r in train}=={0}
    assert {r['label'] for r in test}=={0,1}
    assert [r['timestamp'] for r in train]==sorted(r['timestamp'] for r in train)
    assert train[9]['value']==9. and train[9]['source_2']==90.
    assert len(contract['sources'])==2
    assert [len(s['segment_ids']) for s in contract['sources']]==[2,2]
    assert contract['feature_columns']==['value','source_2']


def test_refuse_train_test_role_mixing_and_stale_contract(tmp_path):
    path=build(tmp_path)
    _,meta=load_matrix(path,channel_ids=[1],role='train')
    with pytest.raises(ValueError,match='身份|时基'):
        load_matrix(path,channel_ids=[2],role='test',expected=meta['sources'])
    with sqlite3.connect(path) as c:
        c.execute("UPDATE v2_samples SET value_num=value_num+1 WHERE channel_id=1 AND segment_id=(SELECT id FROM v2_segments WHERE role='train' AND channel_id=1 LIMIT 1)")
    with pytest.raises(ValueError,match='发生变化'):
        load_matrix(path,channel_ids=[1],role='train',expected=meta['sources'])


def test_exact_previous_nearest_and_missing_samples(tmp_path):
    path=build(tmp_path,domain='numeric_unspecified')
    # Shift one channel's numeric clock forward by 0.5 seconds, with correct per-segment clock metadata.
    with sqlite3.connect(path) as c:
        c.execute("UPDATE v2_samples SET time_sort_num=time_sort_num+0.5,timestamp_raw=CAST(time_sort_num+0.5 AS TEXT) WHERE channel_id=2")
    with pytest.raises(ValueError,match='没有可对齐'):
        load_matrix(path,channel_ids=[1,2],role='train')
    previous,_=load_matrix(path,channel_ids=[1,2],role='train',method='previous',tolerance_seconds=1)
    nearest,_=load_matrix(path,channel_ids=[1,2],role='train',method='nearest',tolerance_seconds=1)
    assert len(previous)==19 and len(nearest)==20
    assert previous[0]['timestamp']=='1'  # no future observations
    assert nearest[0]['timestamp']=='0'


def test_invalid_request_cannot_submit_physical_table(tmp_path):
    path=build(tmp_path)
    good=dict(channel_ids=[1,2],algorithm='isolation_forest_detector',train_ratio=.8,
              method='exact',tolerance_seconds=0)
    assert validate_training_selection(path,good)['role']=='train'
    with pytest.raises(ValueError,match='旧数据表'):
        validate_training_selection(path,{**good,'table':'legacy_train_01'})
    with pytest.raises(ValueError,match='train|test'):
        load_matrix(path,channel_ids=[1],role='raw')


def test_conflicting_duplicate_timestamp_refused(tmp_path):
    path=build(tmp_path)
    with sqlite3.connect(path) as c:
        sample=c.execute("SELECT segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json FROM v2_samples WHERE channel_id=1 ORDER BY id LIMIT 1").fetchone()
        p=list(sample);p[2]=999;p[9]=9999
        c.execute("INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",p)
    with pytest.raises(ValueError,match='冲突'):
        load_matrix(path,channel_ids=[1],role='train')


def test_no_legacy_ui_and_runner_uses_only_logical_ids():
    root=Path(__file__).resolve().parents[1]
    browser=(root/'web/visualization/browser.html').read_text('utf-8')
    runner=(root/'web/visualization/runner.html').read_text('utf-8')
    js=(root/'web/visualization/runner-v2.js').read_text('utf-8')
    assert 'legacyBrowserTools' not in browser and 'browser.js?unified' not in browser
    assert 'tableSelect' not in runner and 'runner.js?' not in runner
    assert '/api/v2/training/start' in js and '/api/v2/evaluation/run' in js
    assert '/api/sqlite-tables' not in js and 'feature_keys' in js and 'dataset_selection' in js


def test_retirement_requires_migration_and_verifies_backup(tmp_path):
    root=tmp_path/'AAS';datasets=root/'datasets';datasets.mkdir(parents=True)
    db=datasets/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as c:
        create_v2(c)
        c.execute("INSERT INTO v2_channels(namespace,model,subsystem,device,variable) VALUES ('M','GF1','ADCS','RW1','speed')")
        c.execute("INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count) VALUES('N','history_sample.db','hash',1,'today',1,1)")
        sid=c.execute('SELECT id FROM v2_sources').fetchone()[0]
        c.execute("INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,usable_for_timeseries,columns_json,source_row_count) VALUES (?, 'part', 1, 'telemetry','train','user_explicit',1,'[]',1)",(sid,))
        c.execute("INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,time_domain,value_raw_json,label_raw_json,raw_row_json) VALUES (1,1,1,'datetime_utc','1','0','{}')")
    old=datasets/'history_sample.db';old.write_bytes(b'legacy-records')
    before=hashlib.sha256(old.read_bytes()).hexdigest()
    assert retire(root)['mode']=='dry-run' and old.exists()
    result=retire(root,apply=True)
    assert not old.exists()
    assert (Path(result['backups'])/'history_sample.db').is_file()
    assert hashlib.sha256((Path(result['backups'])/'history_sample.db').read_bytes()).hexdigest()==before
    assert db.is_file()


def test_datetime_naive_nearest_and_previous_are_numeric_deltas(tmp_path):
    from datetime import datetime, timedelta
    path=build(tmp_path)
    # Simulate a legacy naïve-time migration. Both channels use the same
    # time domain/source, but only the second channel is offset by 500 ms.
    with sqlite3.connect(path) as c:
        c.execute("UPDATE v2_segments SET time_domain='datetime_naive'")
        samples=c.execute('SELECT id,channel_id,timestamp_raw FROM v2_samples').fetchall()
        for sid,cid,stamp in samples:
            date=datetime.fromisoformat(stamp.replace('Z',''))
            if cid==2:date+=timedelta(milliseconds=500)
            normalized=date.isoformat(timespec='microseconds')
            c.execute('''UPDATE v2_samples SET time_domain='datetime_naive',
                timestamp_raw=?,time_sort_text=?,time_utc_us=NULL WHERE id=?''',
                (normalized,normalized,sid))
    with pytest.raises(ValueError,match='没有可对齐'):
        load_matrix(path,channel_ids=[1,2],role='train')
    prev,_=load_matrix(path,channel_ids=[1,2],role='train',method='previous',tolerance_seconds=1)
    near,_=load_matrix(path,channel_ids=[1,2],role='train',method='nearest',tolerance_seconds=1)
    assert len(prev)==19 and len(near)==20
    assert prev[0]['timestamp'].endswith('01.000000')


def test_single_v2_home_summary_with_separate_sparse_simulation_entrypoint():
    from threading import Lock
    from scripts.server.home_stats import snapshot
    root=Path(__file__).resolve().parents[1]
    stats=snapshot(root,{},Lock())
    assert stats['databases'] in (0,1,2)
    assert stats['tables'] >= 0
    assert all(not entry['name'].endswith('.db') for entry in stats['database_items'])
    workbench=(root/'web/visualization/workbench.js').read_text('utf8')
    homepage=(root/'web/visualization/home.html').read_text('utf8')
    handlers=(root/'scripts/server/handlers.py').read_text('utf8')
    assert "key: 'simulation'" in workbench
    assert '/api/v2/simulation/inject' in handlers
    assert 'index.html#simulation' not in homepage
    assert "parsed.path.startswith('/api/simulation/')" in handlers
    assert '/api/v2/training/start' in handlers
