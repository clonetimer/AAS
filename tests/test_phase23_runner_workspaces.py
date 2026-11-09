"""Phase 23: data previews must be independently viewable from task results."""
from pathlib import Path
import pytest

from scripts.server.v2_run_dataset import preview_plot_series

ROOT=Path(__file__).resolve().parents[1]


def _fixture(rows_per_run=10):
    runs=[{'source_id':1,'model_alias':'SAT-01'},{'source_id':2,'model_alias':'SAT-02'}]
    rows=[]
    for run in runs:
        for t in range(rows_per_run):
            rows.append({'run_source_id':run['source_id'],'time_s':t,'value':run['source_id']*10+t,
                         'source_2':run['source_id']*100+t,'label':int(run['source_id']==2 and t>=5)})
    return rows,{'runs':runs,'feature_keys':['rw.speed','rw.current']}


def test_two_runs_are_independent_and_features_preserve_order():
    rows,contract=_fixture()
    result=preview_plot_series(rows,contract)
    assert [g['source_id'] for g in result]==[1,2]
    assert [g['model_alias'] for g in result]==['SAT-01','SAT-02']
    assert result[0]['points'][0]=={'time_s':0,'label':0,'values':[10,100]}
    assert result[1]['points'][-1]=={'time_s':9,'label':1,'values':[29,209]}
    assert rows[0]['value']==10   # no mutations


def test_bounded_sampling_includes_last_sample():
    rows,contract=_fixture(600)
    result=preview_plot_series(rows,contract,max_points=100)
    assert sum(r['plotted_count'] for r in result)<=100
    assert [r['original_count'] for r in result]==[600,600]
    assert result[0]['points'][0]['time_s']==0
    assert result[0]['points'][-1]['time_s']==599
    assert result[1]['points'][-1]['time_s']==599


def test_contract_mismatch_and_budget_validate():
    rows,contract=_fixture()
    with pytest.raises(ValueError,match='绘图数据来源'):
        preview_plot_series([{**rows[0],'run_source_id':99}],contract)
    with pytest.raises(ValueError,match='限制'):
        preview_plot_series(rows,contract,max_points=1)


def test_ui_has_separate_workspaces_and_no_legacy_table_picker():
    html=(ROOT/'web/visualization/runner.html').read_text(encoding='utf-8')
    js=(ROOT/'web/visualization/runner-v2.js').read_text(encoding='utf-8')
    for token in ('trainDataPanel','trainProgressPanel','trainResultPanel','evalDataPanel','evalResultPanel',
                  'trainDataTab','trainProgressTab','trainResultTab','evalDataTab','evalResultTab'):
        assert token in html
    for token in ('setWorkspace','renderDataPlot','previewCache','trainingSnapshot','evaluationSnapshot',
                  'trainProgressBar','plot_series'):
        assert token in js
    assert 'tableSelect' not in html


def test_route_uses_real_selection_for_plot_preview():
    route=(ROOT/'scripts/server/telemetry_v2_routes.py').read_text(encoding='utf-8')
    assert "rows,meta=load_selection(_db(main)" in route
    assert "'plot_series':preview_plot_series(rows,meta)" in route


def test_http_preview_payload_includes_actual_bounded_curves(monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts.server import telemetry_v2_routes as routes
    rows,contract=_fixture(100)
    contract.update({'aligned_count':200,'normal_count':195,'fault_count':5,
                     'time_window':{'start':0,'end':99}})
    monkeypatch.setattr(routes,'_db',lambda main: Path('unused'))
    monkeypatch.setattr(routes,'load_selection',lambda *a,**kw:(rows,contract))

    class Main:
        @staticmethod
        def json_response(handler, payload, status=200):
            return payload
    body=json.dumps({'role':'train','dataset_selection':{'source_ids':[1,2]}}).encode()
    result=routes.handle_post(None,SimpleNamespace(path='/api/v2/run-dataset/preview'),Main(),body)
    assert result['aligned_count']==200
    assert result['feature_keys']==['rw.speed','rw.current']
    assert len(result['plot_series'])==2
    assert result['plot_series'][0]['points'][0]['values']==[10,100]
    assert result['plot_series'][1]['points'][-1]['time_s']==99
