import sqlite3

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection
from scripts.server.v2_score_only import load_score_only_rows
from scripts.server.telemetry_v2_source_import import source_operation_file
from scripts.server.v2_user_csv_adapter import commit_user_csv
from scripts.server.telemetry_v2_routes import _resolve_data_source
from types import SimpleNamespace


def test_frame_import_and_missing_values(tmp_path):
    path=tmp_path/'frames.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    payload={'coordinate_mode':'frame','namespace':'demo','model':'M','subsystem':'S',
             'device':'D','role':'test','filename':'test.csv','unit':'rad/s',
             'csv':'frame,gyro_x,gyro_y,label\n1,0.12,0.31,0\n2,,0.33,0\n4,0.85,0.91,1\n',
             'frame_sequence_id':'dataset-A'}
    preview=import_wide_csv(path,payload)
    assert preview['missing_frames']==1
    assert preview['missing_by_column']=={'gyro_x':1}
    result=import_wide_csv(path,payload,commit=True)
    with sqlite3.connect(path) as conn:
        rows=conn.execute('''SELECT s.frame_index,s.timestamp_raw,s.value_num,s.label_int,s.quality_flag
            FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
            WHERE g.source_id=? AND g.source_table='gyro_x' ORDER BY s.frame_index''',(result['source_id'],)).fetchall()
        assert rows==[(1,None,0.12,0,'ok'),(2,None,None,0,'missing'),(4,None,0.85,1,'ok')]


def test_frame_training_dataset_and_isolation(tmp_path):
    path=tmp_path/'run.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    base={'coordinate_mode':'frame','namespace':'demo','model':'M','subsystem':'S',
          'device':'D','unit':'rad/s','frame_sequence_id':'same','filename':'a.csv'}
    header='frame,gyro_x,gyro_y,label\n'
    train=import_wide_csv(path,{**base,'role':'train','csv':header+''.join(
        f'{i},{i/10},{i/20},0\n' for i in range(1,13))},commit=True)
    test=import_wide_csv(path,{**base,'role':'test','filename':'b.csv','csv':header+''.join(
        f'{i},{i/5},{i/7},{int(i>6)}\n' for i in range(1,13))},commit=True)
    keys=['S.D.gyro_x','S.D.gyro_y']
    selection=lambda sid:{'coordinate_mode':'frame','frame_sequence_id':'same',
        'source_ids':[sid],'feature_keys':keys,'method':'exact','tolerance_seconds':0}
    assert {run['source_id'] for run in catalog(path)['runs']}=={train['source_id'],test['source_id']}
    preview=validate_training_selection(path,{'dataset_selection':selection(train['source_id'])})
    assert preview['aligned_count']==12 and preview['coordinate_contract']['mode']=='frame'
    rows,contract=load_selection(path,selection=selection(test['source_id']),role='test',
        expected={'feature_keys':keys,'train_source_ids':[train['source_id']],
                  'train_source_digests':[train['sha256']],
                  'train_groups':[preview['runs'][0]['group_id']],
                  'coordinate_mode':'frame','feature_schema':preview['feature_schema']})
    assert len(rows)==12 and sum(r['label'] for r in rows)==6
    assert all(r['time_s'] is None for r in rows)


def test_unlabeled_frame_inference_has_no_truth(tmp_path):
    path=tmp_path/'unknown.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    base={'coordinate_mode':'frame','namespace':'demo','model':'M','subsystem':'S',
          'device':'D','unit':'rad/s','frame_sequence_id':'same'}
    train=import_wide_csv(path,{**base,'role':'train','filename':'train.csv',
        'csv':'frame,x,label\n'+''.join(f'{i},{i/10},0\n' for i in range(1,13))},commit=True)
    unknown=import_wide_csv(path,{**base,'role':'test','filename':'unknown.csv',
        'unlabeled_test_confirmed':True,
        'csv':'frame,x\n'+''.join(f'{i},{i/20}\n' for i in range(1,13))},commit=True)
    sel=lambda sid:{'coordinate_mode':'frame','frame_sequence_id':'same',
        'source_ids':[sid],'feature_keys':['S.D.x']}
    _,contract=load_selection(path,selection=sel(train['source_id']),role='train')
    rows,meta=load_score_only_rows(path,selection=sel(unknown['source_id']),expected={
        'feature_keys':['S.D.x'],'coordinate_mode':'frame',
        'train_source_ids':[train['source_id']],
        'train_groups':[contract['runs'][0]['group_id']],
        'train_source_digests':[train['sha256']],
        'feature_schema':contract['feature_schema']})
    assert len(rows)==12 and all('label' not in row for row in rows)
    assert meta['ground_truth_available'] is False


def test_staged_file_frame_route(tmp_path):
    path=tmp_path/'staged.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    source=tmp_path/'input.csv'
    source.write_text('frame,x,label\n1,0.1,0\n2,0.2,0\n',encoding='utf-8')
    config={'coordinate_mode':'frame','frame_sequence_id':'run-A','namespace':'demo',
            'model':'M','subsystem':'S','device':'D','unit':'rad/s','role':'train',
            'column_metadata':{'x':{'subsystem':'S','device':'D','variable':'x','unit':'rad/s'}}}
    common={'filename':'input.csv','config':config,'csv_encoding':'utf-8',
            'csv_delimiter':',','format_confirmed':True}
    preview=source_operation_file(path,source,{**common,'operation':'preview'})
    assert preview['coordinate_mode']=='frame' and preview['row_count']==2
    result=source_operation_file(path,source,{**common,'operation':'commit'})
    assert result['status']=='imported' and result['source_id']>0


def test_duplicate_frame_rejected_and_row_order_requires_confirmation(tmp_path):
    path=tmp_path/'negative.sqlite'
    with sqlite3.connect(path) as conn:create_v2(conn)
    base={'coordinate_mode':'frame','namespace':'demo','model':'M','subsystem':'S',
          'device':'D','unit':'rad/s','role':'train','filename':'x.csv'}
    for payload in (
        {**base,'csv':'frame,x,label\n1,1,0\n1,2,0\n'},
        {**base,'csv':'x,label\n1,0\n2,0\n'}):
        try:
            import_wide_csv(path,payload,commit=True)
            assert False, 'invalid frame CSV was accepted'
        except ValueError:
            pass
    result=import_wide_csv(path,{**base,'row_order_confirmed':True,
        'csv':'x,label\n1,0\n2,0\n'},commit=True)
    with sqlite3.connect(path) as conn:
        assert [row[0] for row in conn.execute('SELECT frame_index FROM v2_samples WHERE segment_id=? ORDER BY source_ordinal',
                                               (result['channels'][0]['segment_id'],))]==[1,2]


def test_user_workspace_route_isolated_from_history(tmp_path):
    raw=b'frame,x,label\n1,0.1,0\n2,0.2,0\n'
    config={'coordinate_mode':'frame','frame_sequence_id':'run-A','namespace':'USER_CSV_frame_test',
            'model':'M','subsystem':'S','device':'D','unit':'rad/s','role':'train',
            'selected_columns':['x'],
            'column_metadata':{'x':{'subsystem':'S','device':'D','variable':'x','unit':'rad/s'}}}
    result=commit_user_csv(tmp_path,raw,'input.csv',{'operation':'commit','filename':'input.csv',
        'config':config,'csv_encoding':'utf-8','csv_delimiter':',','format_confirmed':True},role='train')
    request={'data_source':{'type':'user_csv','dataset_id':result['dataset_id']},
             'dataset_selection':{'source_ids':[result['source_id']]}}
    path,reference,provenance,record=_resolve_data_source(SimpleNamespace(ROOT=tmp_path),request,'train')
    assert path.is_file() and provenance['type']=='user_csv' and record['source_id']==result['source_id']
    assert not (tmp_path/'datasets'/'aas_telemetry_v2.sqlite').exists()
