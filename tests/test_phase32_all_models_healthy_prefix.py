"""Phase 32: model completeness, safe pre-fault selection and unchanged layout."""
from pathlib import Path
import hashlib
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.annotate_uploaded_targets_v2 import SCHEMA as TARGET_SCHEMA
from scripts.server.v2_simulation_injection import (
    SIM_NAME, v2_source_catalog, v2_injection_time_bounds,
    preview_v2_injection, save_v2_injection,
)


def make_fixture(tmp_path):
    folder=tmp_path/'datasets'
    folder.mkdir()
    file=folder/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(file) as db:
        db.executescript(CANON_SCHEMA)
        db.executescript(TARGET_SCHEMA)
        db.execute("INSERT INTO v2_meta(key,value) VALUES('schema_version','2')")
        for n in range(1,20):
            model=f'SIM-SAT-{n:03d}'
            run_role='NORMAL' if n in (5,6,9,10,15) else 'FAULT'
            role='simulation'
            healthy=620 if run_role=='NORMAL' else 610
            sid=db.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
                  imported_utc,source_table_count,source_row_count) VALUES(?,?,?,?,?,?,?)''',
                  (f'run_{n}',f'{model}.csv','abcd',999,'2026-10-23',1,620)).lastrowid
            db.execute('''INSERT INTO v2_run_annotations(source_id,token,model_alias,model_identity_kind,
                split,group_id,run_role,fault_type,fault_component,onset_s,fault_end_exclusive_s,
                normal_frames,fault_frames,annotation_scope,metadata_not_model_features,
                target_sha256,target_archive_sha256,provenance)
                VALUES(?,?,?,'synthetic_alias','train',?,?,?,?,?,?,?,?, 'simulation_run',1,?,?,?)''',
                (sid,f'token-{n}',model,f'group-{n}',run_role,
                 None if run_role=='NORMAL' else 'fault', None if run_role=='NORMAL' else 'adcs',
                 None if run_role=='NORMAL' else 1220.0, None if run_role=='NORMAL' else 1240.0,
                 healthy,620-healthy,'abcd','abcd','test'))
            cid=db.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable,unit)
                VALUES(?,'',?,'adcs','reaction_wheel','speed','rad/s')''',
                (f'run_{n}',model)).lastrowid
            seg=db.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
               time_domain,usable_for_timeseries,columns_json,source_row_count)
               VALUES(?, 'speed', ?, 'telemetry', ?, 'user_explicit','numeric_unspecified',1,'[]',620)''',
               (sid,cid,role)).lastrowid
            db.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,
                timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,
                label_raw_json,raw_row_json) VALUES(?,?,?,?,'numeric','numeric_unspecified',?,?,?,?,?,?)''',
                [(seg,cid,i,str(i*2),float(i*2),float(i),str(i),0 if i<healthy else 1,
                  '0' if i<healthy else '1','{}') for i in range(620)])
        db.commit()
    return file


def test_all_19_models_including_fault_labeled_runs_can_be_selected(tmp_path):
    path=make_fixture(tmp_path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    result=v2_source_catalog(tmp_path)
    assert len({r['model'] for r in result['items']})==19
    assert len(result['items'])==19
    assert sum(x['source_run_role']=='FAULT' for x in result['items'])==14
    assert all(x['source_role']=='simulation' for x in result['items'])
    src=next(x for x in result['items'] if x['model']=='SIM-SAT-001')
    bounds=v2_injection_time_bounds(tmp_path,source_id=src['source_id'],channel_id=src['channel_id'],max_points=620)
    assert bounds['sampling_rows']==610
    assert bounds['baseline_truncated'] is True
    assert bounds['end_inclusive']==1218
    config={'source_id':src['source_id'],'channel_id':src['channel_id'],
            'fault_type':'bias','onset_s':400,'magnitude':18,'max_points':620}
    prev=preview_v2_injection(tmp_path,config)
    assert len(prev['points'])==610
    assert sum(x['label'] for x in prev['points'])==410
    assert all(x['t']<1220 for x in prev['points'])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    saved=save_v2_injection(tmp_path,{**config,'preview_sha256':prev['preview_sha256']})
    assert saved['source_model']=='SIM-SAT-001'
    assert saved['source_database_copied'] is False
    assert (tmp_path/'datasets'/SIM_NAME).is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest


def test_unlabeled_and_preexisting_faults_never_enter_normal_baseline(tmp_path):
    path=make_fixture(tmp_path)
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT g.id FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id WHERE c.model='SIM-SAT-019'").fetchone()
        db.execute('UPDATE v2_samples SET label_int=NULL WHERE segment_id=? AND source_ordinal=0',(row[0],))
        row2=db.execute("SELECT g.id FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id WHERE c.model='SIM-SAT-018'").fetchone()
        db.execute('UPDATE v2_samples SET label_int=1 WHERE segment_id=? AND source_ordinal=8',(row2[0],))
    catalog=v2_source_catalog(tmp_path)
    models={x['model'] for x in catalog['items']}
    assert 'SIM-SAT-019' not in models
    assert 'SIM-SAT-018' in models # first eight explicitly healthy
    src=next(x for x in catalog['items'] if x['model']=='SIM-SAT-018')
    with pytest.raises(ValueError,match='不足 8|故障开始时刻'):
        preview_v2_injection(tmp_path,{'source_id':src['source_id'],'channel_id':src['channel_id'],
            'fault_type':'bias','onset_s':400,'magnitude':18,'max_points':600})


def test_page_layout_is_unchanged():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/simulation.html').read_text(encoding='utf-8')
    js=(root/'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    assert html.count('class="sim-card"')==2
    assert 'id="simModel"' in html and 'id="simPreview"' in html
    assert 'baseline_truncated' in js and 'source_role' in js
