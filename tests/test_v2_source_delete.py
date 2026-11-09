import json,sqlite3
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def raises(expected):
    try:
        yield
    except expected:
        return
    raise AssertionError("expected " + expected.__name__)
from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.v2_source_delete import preview,delete,list_sources


def _database(tmp_path):
    root=tmp_path
    (root/'datasets').mkdir()
    db=root/'datasets'/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(db) as conn:create_v2(conn)
    base={'coordinate_mode':'frame','namespace':'demo','model':'M','subsystem':'S',
          'device':'D','unit':'rad/s','frame_sequence_id':'trial','role':'test'}
    a=import_wide_csv(db,{**base,'filename':'a.csv','csv':'frame,x,label\n1,1,0\n2,2,1\n'},commit=True)
    b=import_wide_csv(db,{**base,'filename':'b.csv','csv':'frame,x,label\n1,3,0\n2,4,1\n'},commit=True)
    return root,db,a,b


def test_source_delete_preserves_other_sources_and_shared_channels(tmp_path):
    root,db,a,b=_database(tmp_path)
    sid=a['source_id'];other=b['source_id']
    assert len(list_sources(db)['items'])==2
    before=preview(root,db,sid)
    assert before['deletable'] and before['samples']==2
    with raises(ValueError):
        delete(root,db,sid,preview_token='0'*64,confirm_name=before['source_name'])
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==2
    result=delete(root,db,sid,preview_token=before['preview_token'],confirm_name=before['source_name'])
    assert result['deleted'] and result['samples']==2
    with sqlite3.connect(db) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        assert conn.execute('SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id WHERE g.source_id=?',(other,)).fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM v2_source_deletion_audit').fetchone()[0]==1
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    with raises(KeyError):preview(root,db,sid)


def test_source_delete_blocks_model_and_derived_source(tmp_path):
    root,db,a,b=_database(tmp_path)
    sid=a['source_id'];other=b['source_id']
    (root/'models').mkdir()
    with sqlite3.connect(root/'models'/'model.db') as conn:
        conn.execute('CREATE TABLE models(id INTEGER,model_name TEXT,dataset TEXT,config_json TEXT)')
        conn.execute('INSERT INTO models VALUES(1,?,?,?)',('uses-a',db.name,json.dumps({'data_contract':{'v2_run_selection':{'source_ids':[sid]}}})))
    blocked=preview(root,db,sid)
    assert blocked['blockers'][0]['type']=='model'
    with raises(RuntimeError):delete(root,db,sid,preview_token=blocked['preview_token'],confirm_name=blocked['source_name'])
    with sqlite3.connect(root/'models'/'model.db') as conn:conn.execute('DELETE FROM models')
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE v2_preprocess_quality(output_segment_id INTEGER REFERENCES v2_segments(id),input_segment_id INTEGER)')
        input_seg=conn.execute('SELECT id FROM v2_segments WHERE source_id=?',(sid,)).fetchone()[0]
        output_seg=conn.execute('SELECT id FROM v2_segments WHERE source_id=?',(other,)).fetchone()[0]
        conn.execute('INSERT INTO v2_preprocess_quality VALUES(?,?)',(output_seg,input_seg))
    derived=preview(root,db,sid)
    assert derived['blockers'][0]['type']=='derived_source'
    with raises(RuntimeError):delete(root,db,sid,preview_token=derived['preview_token'],confirm_name=derived['source_name'])
    with sqlite3.connect(db) as conn:conn.execute('DELETE FROM v2_preprocess_quality')
    allowed=preview(root,db,sid)
    with raises(RuntimeError):delete(root,db,sid,preview_token=allowed['preview_token'],confirm_name=allowed['source_name'],active_tasks=True)
    with sqlite3.connect(db) as conn:assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==2


def test_source_delete_route_contract(tmp_path):
    from types import SimpleNamespace
    from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES
    root,db,a,b=_database(tmp_path)
    main=SimpleNamespace(ROOT=root,json_response=lambda _h,data,status=200:(status,data),
                         list_public_task_statuses=lambda limit,include_all=False:[])
    parsed=SimpleNamespace(query='database=aas_telemetry_v2.sqlite',
                           path='/api/v2/telemetry/sources')
    status,result=GET_DATA_ROUTES[parsed.path](None,parsed,main)
    assert status==200 and len(result['items'])==2
    parsed.path='/api/v2/telemetry/source/delete/preview'
    body=json.dumps({'database':db.name,'source_id':a['source_id']}).encode()
    status,pre=POST_DATA_ROUTES[parsed.path](None,parsed,main,body)
    assert status==200 and pre['deletable']
    parsed.path='/api/v2/telemetry/source/delete/commit'
    body=json.dumps({'database':db.name,'source_id':a['source_id'],
        'preview_token':pre['preview_token'],'confirm_name':pre['source_name'],
        'confirm':True}).encode()
    status,result=POST_DATA_ROUTES[parsed.path](None,parsed,main,body)
    assert status==200 and result['deleted']
    assert len(list_sources(db)['items'])==1


def test_unrelated_model_does_not_block_source_delete(tmp_path):
    root,db,a,b=_database(tmp_path)
    (root/'models').mkdir()
    with sqlite3.connect(root/'models'/'model.db') as conn:
        conn.execute('CREATE TABLE models(id INTEGER,model_name TEXT,dataset TEXT,config_json TEXT)')
        conn.execute('INSERT INTO models VALUES(1,?,?,?)',('uses-b',db.name,json.dumps({
            'data_contract':{'v2_run_selection':{'source_ids':[b['source_id']]}}})))
    model_hash_before=__import__('hashlib').sha256((root/'models'/'model.db').read_bytes()).hexdigest()
    pre=preview(root,db,a['source_id'])
    assert pre['deletable']
    result=delete(root,db,a['source_id'],preview_token=pre['preview_token'],confirm_name=pre['source_name'])
    assert result['deleted']
    assert __import__('hashlib').sha256((root/'models'/'model.db').read_bytes()).hexdigest()==model_hash_before
    assert preview(root,db,b['source_id'])['deletable'] is False
