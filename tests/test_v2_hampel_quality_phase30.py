"""V2 quality contract: bounded forward fill, provenance, UI, and training preflight."""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_preprocess import preprocess, list_quality_jobs
from scripts.server.telemetry_v2_training import load_matrix
from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection
from scripts.server.v2_quality import hampel_forward


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'telemetry.sqlite'
    with sqlite3.connect(path) as conn:
        create_v2(conn)
    return path


def upload(path, values, *, role='train', name='run'):
    lines=['timestamp,value,label']
    for i,value in enumerate(values):
        lines.append(f'{i},{"" if value is None else value},{int(role=="test" and i>=len(values)//2)}')
    return import_csv(path, dict(namespace='M1',model='GF1',subsystem='ADCS',device='RW',
        variable='speed',unit='rpm',role=role,time_domain='numeric_unspecified',
        time_reference=name,time_unit='s',filename=name+'.csv',csv='\n'.join(lines)+'\n'),commit=True)


def test_hampel_forward_end_to_end_persist_quality_and_train(db):
    vals=[None,5,5,5,999,5,None,None,None,None,5,5,5,5]
    inp=upload(db,vals)
    assert inp['missing_count']==5
    req={'channel_id':inp['channel_id'],'role':'train','method':'hampel_forward',
         'params':{'window':7}}
    pre=preprocess(db,req)
    assert pre['quality']['total']==len(vals)
    assert pre['quality']['outliers']==1
    assert pre['quality']['source_missing']==5
    assert pre['quality']['forward_filled']==4
    assert pre['quality']['unfilled']==2
    assert pre['quality']['valid_samples']==12
    assert pre['sample_check']['eligible_for_training']
    assert pre['plot_series'][4]['source']==999
    assert pre['plot_series'][4]['processed']==5
    assert pre['plot_series'][9]['processed'] is None
    assert len(pre['plot_series'])==len(vals)
    with sqlite3.connect(db) as conn:
        before=conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]
    assert before==len(vals)
    made=preprocess(db,{**req,'confirm':True},commit=True,actor='test')
    with sqlite3.connect(db) as conn:
        originals=[r[0] for r in conn.execute('SELECT value_num FROM v2_samples WHERE segment_id=? ORDER BY source_ordinal',(inp['segment_id'],))]
        assert originals==vals
        saved=[r[0] for r in conn.execute('SELECT value_num FROM v2_samples WHERE segment_id=? ORDER BY source_ordinal',(made['output_segment_id'],))]
        assert saved==[None,5.,5.,5.,5.,5.,5.,5.,5.,None,5.,5.,5.,5.]
        states=[r[0] for r in conn.execute('SELECT status FROM v2_preprocess_quality WHERE output_segment_id=? ORDER BY source_ordinal',(made['output_segment_id'],))]
        assert states[0]=='unfilled_missing'
        assert states[4]=='filled_outlier'
        assert states[8]=='filled_missing'
        assert states[9]=='unfilled_missing'
        sample=conn.execute('SELECT raw_row_json FROM v2_samples WHERE segment_id=? AND source_ordinal=4',(made['output_segment_id'],)).fetchone()[0]
        assert json.loads(sample)['quality']=='filled_outlier'
    assert list_quality_jobs(db)['items'][0]['quality']['invalid_samples']==2
    checked,meta=load_matrix(db,channel_ids=[made['output_channel_id']],role='train')
    assert len(checked)==12
    assert meta['sample_check']['per_channel'][0]['invalid_samples']==2
    runs=catalog(db)['runs']
    derived_source=next(r['source_id'] for r in runs if r['source_id']!=inp['segment_id'] and r['model_alias'].endswith('run'))
    selected={'source_ids':[derived_source],'feature_keys':['ADCS.RW.speed__hampel_forward'],
              'method':'exact','tolerance_seconds':0}
    records,contract=load_selection(db,selection=selected,role='train')
    assert len(records)==12
    assert contract['sample_check']['unfilled_or_invalid_source_samples']==2
    validate_training_selection(db,{'dataset_selection':selected})
    with pytest.raises(ValueError,match='已经生成'):
        preprocess(db,{**req,'confirm':True},commit=True)


def test_hampel_no_backward_fill_and_gap_threshold(db):
    inp=upload(db,[None,1,None,None,None,None,1,1,1,1])
    made=preprocess(db,{'channel_id':inp['channel_id'],'role':'train','method':'hampel_forward'})
    assert [v['processed'] for v in made['plot_series']][:6]==[None,1.,1.,1.,1.,None]
    assert made['quality']['unfilled']==2


def test_no_carry_across_segments_and_time_discontinuity():
    rows=[]
    for idx,(seg,stamp,value) in enumerate([(1,0,4.),(1,1,None),(2,2,None),(2,3,9.),
                                             (2,4,None),(2,5,None),(2,100,None)]):
        rows.append({'segment_id':seg,'source_ordinal':idx,'time_key':stamp,
                     'timestamp_raw':str(stamp),'value_num':value})
    points,quality,_=hampel_forward(rows)
    assert [x[1] for x in points]==[4.,4.,None,9.,9.,9.,None]
    assert [q['status'] for q in quality][2]=='unfilled_missing'
    assert quality[-1]['status']=='unfilled_missing'


def test_invalid_config_and_missing_import_modes(db):
    inp=upload(db,[1,1,None,1,1,1,1,1])
    for bad in ({'window':4},{'window':2},{'window':7,'max_consecutive_fill':10},
                {'window':7,'keep_quality_flags':False}):
        with pytest.raises(ValueError):
            preprocess(db,{'channel_id':inp['channel_id'],'role':'train',
                           'method':'hampel_forward','params':bad})
    with pytest.raises(ValueError,match='Hampel'):
        preprocess(db,{'channel_id':inp['channel_id'],'role':'train','method':'moving_average'})
    with pytest.raises(ValueError,match='必须有限'):
        upload(db,[1,float('inf'),1],name='bad')


def test_test_role_processed_preflight_retains_truth(db):
    values=[5,5,5,5,500,5,None,None,None,None,5,5,5,5]
    inp=upload(db,values,role='test',name='validation')
    made=preprocess(db,{'channel_id':inp['channel_id'],'role':'test','method':'hampel_forward',
            'confirm':True},commit=True)
    _,meta=load_matrix(db,channel_ids=[made['output_channel_id']],role='test')
    assert meta['sample_check']['per_channel'][0]['invalid_samples']==1
    assert meta['aligned_count']==13
    assert meta['role']=='test'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE segment_id=? AND label_int=1',(made['output_segment_id'],)).fetchone()[0]==7


def test_ui_shows_chart_quality_and_v2_requests():
    web=Path(__file__).resolve().parents[1]/'web/visualization'
    html=(web/'preprocess.html').read_text(encoding='utf-8')
    script=(web/'telemetry-v2-preprocess.js').read_text(encoding='utf-8')
    assert 'id="v2PrepRawPlot"' in html and 'id="v2PrepCleanPlot"' in html
    assert 'id="v2PrepReport"' in html and 'v2-quality-ui.js' in html
    assert '<option value="hampel_forward" selected>' in html
    assert 'v2PrepJobsReload' in script and 'aasV2QualityUI?.render(data)' in script


def test_wide_import_keeps_missing_cells_for_offline_quality(db):
    from scripts.server.telemetry_v2_wide_import import import_wide_csv
    payload=dict(namespace='M2',model='GF1',subsystem='ADCS',device='RW',unit='rpm',
        role='train',time_domain='numeric_unspecified',time_reference='wide_01',time_unit='s',
        filename='wide.csv',csv='time_s,ADCS.RW.speed,ADCS.RW.current,label\n0,7,1,0\n1,,2,0\n2,9,NA,0\n3,9,4,0\n')
    preview=import_wide_csv(db,payload)
    assert preview['missing_by_column']=={'ADCS.RW.speed':1,'ADCS.RW.current':1}
    committed=import_wide_csv(db,payload,commit=True)
    selected=committed['channels'][0]['channel_id']
    result=preprocess(db,{'channel_id':selected,'role':'train','method':'hampel_forward'})
    assert result['input_count']==4
    assert result['quality']['source_missing']==1


def test_unfilled_gap_is_alignment_barrier_for_previous_and_nearest(db):
    from scripts.server.telemetry_v2_wide_import import import_wide_csv
    from scripts.server.v2_run_dataset import load_selection
    values=[5.]*5+[None]*5+[5.]*6
    csv_lines=['time_s,ADCS.RW.anchor,ADCS.RW.reference,label']
    for i,value in enumerate(values):
        csv_lines.append(f'{i},{i},{"" if value is None else value},0')
    payload=dict(namespace='barrier',model='GF1',subsystem='ADCS',device='RW',unit='rpm',
                 role='train',time_domain='numeric_unspecified',time_reference='run_barrier',time_unit='s',
                 filename='gap.csv',csv='\n'.join(csv_lines)+'\n')
    original=import_wide_csv(db,payload,commit=True)
    b_channel=original['channels'][1]['channel_id']
    made=preprocess(db,{'channel_id':b_channel,'role':'train','method':'hampel_forward',
        'confirm':True},commit=True)
    for method in ('previous','nearest'):
        # Both methods must see the NULL time keys, rather than hopping to an
        # older finite observation and violating the max-3-fill policy.
        rows,meta=load_matrix(db,channel_ids=[original['channels'][0]['channel_id'],
            made['output_channel_id']],role='train',method=method,tolerance_seconds=2)
        assert {r['timestamp'] for r in rows}.isdisjoint({'8','9'})
        assert meta['sample_check']['per_channel'][1]['invalid_samples']==2
        selected={'source_ids':[original['source_id']],
                  'feature_keys':['ADCS.RW.anchor','ADCS.RW.reference'],
                  'method':method,'tolerance_seconds':2}
        run_rows,run_meta=load_selection(db,selection=selected,role='train')
        assert {r['time_s'] for r in run_rows}.isdisjoint({5.,6.,7.,8.,9.})
        assert run_meta['sample_check']['unfilled_or_invalid_source_samples']==5
