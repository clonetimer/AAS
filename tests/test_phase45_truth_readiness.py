"""Phase45 deterministic evidence audit and copy-only repair; no user DB touched."""
from pathlib import Path
import csv
import json
import sqlite3

import pytest
from tests.test_v2_run_subset_phase8 import labeled

from scripts.audit_v2_fault_truth import (
    DEFAULT_MANIFEST, evidence_manifest, channel_coverage, inspect, apply_to_new_copy)
from scripts.annotate_uploaded_targets_v2 import SCHEMA as RUN_SCHEMA
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.target_fault_location import FAULT_LOCATIONS


def synthetic_manifest_db(tmp_path, count=19):
    manifest=list(evidence_manifest().values())[:count]
    path=tmp_path/'fixture.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        create_v2(conn)
        conn.executescript(RUN_SCHEMA)
        for i, doc in enumerate(manifest, 1):
            conn.execute('''INSERT INTO v2_sources
              (id,namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count)
              VALUES (?,?,?,?,?,?,?,?)''',
              (i,f'source:{i}',f'run{i}.csv',doc['source_sha256'],0,'2026-10-20T00:00:00Z',1,1))
            conn.execute('''INSERT INTO v2_run_annotations
              (source_id,token,model_alias,model_identity_kind,split,group_id,run_role,
               fault_type,fault_component,onset_s,fault_end_exclusive_s,normal_frames,fault_frames,
               annotation_scope,metadata_not_model_features,target_sha256,target_archive_sha256,provenance)
              VALUES (?,?,?,'synthetic_alias',?,?,?,?,?,?,?,?,?,'simulation_run',1,'sha','zipsha','verified')''',
              (i,doc['token'],doc['model_alias'],doc['split'],doc['group'],doc['target_role'],
               doc['fault'] or None,doc['fault_component'] or None,float(doc['onset_s']) if doc['onset_s'] else None,
               float(doc['onset_s'])+50 if doc['onset_s'] else None,int(doc['normal_frames']),int(doc['fault_frames'])))
            conn.execute('''INSERT INTO v2_segments
              (source_id,source_table,kind,role,role_provenance,columns_json,source_row_count,
               usable_for_timeseries) VALUES (?,?,'telemetry','simulation','run_verified','[]',1,1)''',
               (i,f'channel{i}'))
        conn.commit()
    return path


def test_19_manifest_maps_fault_origin_without_manufacturing_parameter_truth():
    docs=evidence_manifest()
    catalog=channel_coverage()
    assert len(docs)==19 and len(catalog)>0
    faults=[r for r in docs.values() if r['target_role']=='FAULT']
    assert len(faults)==14
    assert sum(bool(catalog.get(FAULT_LOCATIONS[r['fault_component']][:2])) for r in faults)==6
    rw=docs['0b78c8d24074988009448977']
    assert rw['model_alias']=='SIM-SAT-014' and rw['fault_component']=='magnetometer'
    assert FAULT_LOCATIONS[rw['fault_component']][:2]==('adcs','magnetometer')
    assert 'adcs.rw.measured_speed_rad_s_0' not in catalog[('adcs','magnetometer')]


def test_copy_repairs_only_verified_fault_origin_and_preserves_original(tmp_path):
    db=synthetic_manifest_db(tmp_path)
    before=db.read_bytes()
    observed=inspect(db)
    assert observed['database_runs']==19 and observed['needs_install']==14
    assert observed['blocked']==0 and observed['direct_observable_faults']==6
    output=tmp_path/'mapped.sqlite'
    ready=apply_to_new_copy(db,output)
    assert ready['needs_install']==0 and ready['blocked']==0
    assert db.read_bytes()==before
    with sqlite3.connect(output) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_run_fault_locations').fetchone()[0]==14
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==0
    with pytest.raises(FileExistsError):apply_to_new_copy(db,output)
    assert inspect(output)['needs_install']==0


def test_broken_source_sha_prevents_new_database(tmp_path):
    db=synthetic_manifest_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE v2_sources SET source_sha256='tampered' WHERE id=14")
    status=inspect(db)
    assert status['blocked']==1
    assert 'source_sha256_mismatch' in status['runs'][13]['errors']
    with pytest.raises(ValueError,match='冲突'):
        apply_to_new_copy(db,tmp_path/'no.sqlite')
    assert not (tmp_path/'no.sqlite').exists()


def test_conflict_and_partial_manifest_are_fail_closed(tmp_path):
    db=synthetic_manifest_db(tmp_path,count=2)
    assert inspect(db)['missing_tokens']
    with pytest.raises(ValueError,match='不完整'):
        apply_to_new_copy(db,tmp_path/'reject.sqlite')
    partial=apply_to_new_copy(db,tmp_path/'partial.sqlite',require_complete=False)
    assert partial['needs_install']==0
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE v2_run_fault_locations(
            source_id INTEGER PRIMARY KEY,subsystem TEXT,device TEXT,classification_basis TEXT,note TEXT)''')
        conn.execute("INSERT INTO v2_run_fault_locations VALUES (1,'adcs','reaction_wheel','unverified','bad')")
    assert inspect(db)['blocked']==1
    with pytest.raises(ValueError):apply_to_new_copy(db,tmp_path/'conflict.sqlite',require_complete=False)


def test_user_supplied_parameter_labels_never_generated_in_migration(tmp_path):
    db=synthetic_manifest_db(tmp_path)
    output=tmp_path/'new.sqlite'
    apply_to_new_copy(db,output)
    with sqlite3.connect(output) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_fault_events'").fetchone()
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_normal_component_target_requires_no_fault_location_table(tmp_path):
    from scripts.server.v2_label_targets import target_for_run
    path=tmp_path/'minimal.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE v2_segments(id INTEGER PRIMARY KEY,channel_id INTEGER)')
        classify,evidence=target_for_run(conn,{'data_role':'simulation','run_role':'NORMAL','source_id':1},
             {'scope':'component','subsystem':'adcs','device':'reaction_wheel'},
             ['adcs.rw.speed'],[('adcs','reaction_wheel','speed','rad/s')],[1])
    assert classify(500)==0 and evidence['source']=='verified_normal_run'


def test_reviewed_events_are_source_scoped_and_used_for_parameter_truth(labeled):
    from tests.test_v2_run_subset_phase8 import choose
    from scripts.server.v2_run_dataset import load_selection
    from scripts.server.v2_source_parameter_events import install_reviewed_events
    rows=[{'token':'run4','feature_key':'adcs.rw.speed_rad_s','start_s':0,'end_s':4,
           'label':0,'reviewer':'reviewer A','evidence_reference':'review:run4:speed:normal'},
          {'token':'run4','feature_key':'adcs.rw.speed_rad_s','start_s':4,'end_s':6,
           'label':1,'fault_type':'observed_speed_event','reviewer':'reviewer A',
           'evidence_reference':'review:run4:speed:event'}]
    with sqlite3.connect(labeled) as conn:
        conn.row_factory=sqlite3.Row
        assert len(install_reviewed_events(conn,rows))==2
        before=conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int=1').fetchone()[0]
        # Prevent accidental update/duplication of the original run-level label.
        assert before==8
    actual,contract=load_selection(labeled,selection=choose([3,4]),role='test',
        label_target={'scope':'parameter','feature_key':'adcs.rw.speed_rad_s'})
    assert len(actual)==6
    assert contract['label_coverage']['excluded_unknown']==6
    assert {r['run_source_id'] for r in actual}=={4}
    assert [r['label'] for r in actual]==[0,0,0,0,1,1]
    assert contract['runs'][1]['label_evidence']['source']=='v2_source_parameter_events'
    # A shared logical channel id would be unsafe for legacy annotations, but
    # source+segment-scoped events do not spread to a different source.
    with sqlite3.connect(labeled) as conn:
        conn.execute('UPDATE v2_segments SET channel_id=7 WHERE id=9')
    actual,_=load_selection(labeled,selection=choose([4]),role='test',
        label_target={'scope':'parameter','feature_key':'adcs.rw.speed_rad_s'})
    assert len(actual)==6


def test_reviewed_source_event_rejects_overlap_invalid_provenance(labeled):
    from scripts.server.v2_source_parameter_events import install_reviewed_events
    entry={'token':'run4','feature_key':'adcs.rw.speed_rad_s','start_s':0,'end_s':4,
           'label':0,'reviewer':'reviewer','evidence_reference':'manual:good'}
    with sqlite3.connect(labeled) as conn:
        conn.row_factory=sqlite3.Row
        install_reviewed_events(conn,[entry])
        with pytest.raises(ValueError,match='重叠'):
            install_reviewed_events(conn,[{**entry,'start_s':2,'end_s':5}])
        with pytest.raises(ValueError,match='证据引用'):
            install_reviewed_events(conn,[{**entry,'start_s':4,'end_s':6,'evidence_reference':''}])
        with pytest.raises(ValueError,match='唯一有效来源'):
            install_reviewed_events(conn,[{**entry,'token':'run3','feature_key':'not a real parameter'}])
        with pytest.raises(ValueError,match='没有对应遥测采样'):
            install_reviewed_events(conn,[{**entry,'start_s':900,'end_s':901}])
        assert conn.execute('SELECT COUNT(*) FROM v2_source_parameter_events').fetchone()[0]==1


def test_parameter_import_copy_does_not_mutate_19_run_original(tmp_path):
    from scripts.import_v2_parameter_truth import apply_events_copy
    db=synthetic_manifest_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE v2_segment_clock(segment_id INTEGER PRIMARY KEY,
            reference TEXT NOT NULL,unit TEXT NOT NULL)''')
        conn.execute('''INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable,unit)
            VALUES (140,'source:14','SIM-SAT-014','adcs','magnetometer','tesla_x','T')''')
        conn.execute("UPDATE v2_segments SET channel_id=140,source_table='adcs.magnetometer.tesla_x', time_domain='numeric_unspecified' WHERE source_id=14")
        seg=conn.execute('SELECT id FROM v2_segments WHERE source_id=14').fetchone()[0]
        conn.execute('INSERT INTO v2_segment_clock VALUES (?,?,?)',(seg,'elapsed_run:SIM-SAT-014','s'))
        for i in range(4):
            conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
                time_domain,time_sort_num,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(seg,140,i,str(i),'numeric_unspecified',float(i),float(i),'0',int(i>=2),'0','{}'))
        conn.commit()
    original=db.read_bytes()
    event_doc=tmp_path/'reviewed.json'
    event_doc.write_text(json.dumps({'events':[
        {'token':'0b78c8d24074988009448977','feature_key':'adcs.magnetometer.tesla_x',
         'start_s':0,'end_s':2,'label':0,'reviewer':'A','evidence_reference':'manual-normal-14'},
        {'token':'0b78c8d24074988009448977','feature_key':'adcs.magnetometer.tesla_x',
         'start_s':2,'end_s':4,'label':1,'fault_type':'reviewed_noise','reviewer':'A',
         'evidence_reference':'manual-fault-14'}]}),encoding='utf-8')
    dest=tmp_path/'reviewed_v2.sqlite'
    summary=apply_events_copy(db,dest,event_doc)
    assert summary['installed_events']==2 and db.read_bytes()==original
    with sqlite3.connect(dest) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_source_parameter_events').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int=1').fetchone()[0]==2
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    bad=tmp_path/'bad.json'
    bad.write_text(json.dumps({'events':[{'token':'0b78c8d24074988009448977',
            'feature_key':'adcs.rw.measured_speed_rad_s_0','start_s':2,'end_s':4,
            'label':1,'fault_type':'reviewed_fault','reviewer':'A','evidence_reference':'guess'}]}))
    with pytest.raises(ValueError,match='唯一有效来源'):
        apply_events_copy(db,tmp_path/'never-created.sqlite',bad)
    assert not (tmp_path/'never-created.sqlite').exists()
