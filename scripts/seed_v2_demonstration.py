"""Execute reproducible *real* training/evaluation tasks and archive demo reports.

Requires the labeled 19-run canonical V2 source database to be installed.
Generated model, metrics and curves are taken from the actual algorithm; no
invented classification scores or frontend-only mock experiment records.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import visualization_server as main
from scripts.server.telemetry_v2_routes import _evaluation
from scripts.server.v2_run_dataset import load_selection
from scripts.server.result_traces import chart_trace
from scripts.server.v2_database_config import active_path, active_name
from scripts.server.v2_simulation_injection import seed_default

KEYS=['adcs.rw.measured_speed_rad_s_0','adcs.rw.measured_speed_rad_s_1']


def selection(ids,start,end):
    return {'source_ids':ids,'feature_keys':KEYS,'start':start,'end':end,'method':'exact','tolerance_seconds':0}


def _save_report(kind, experiment_id, algorithm, model, dataset, result, select, metrics):
    report_dir=ROOT/'outputs'/('experiment_'+experiment_id)/'reports'
    report_dir.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(result,ensure_ascii=False,sort_keys=True,default=str).encode('utf-8')
    with gzip.open(report_dir/'execution_result.json.gz','wb') as gz:gz.write(raw)
    summary={
      'experiment':{'experiment_id':experiment_id,'type':kind,
        'run_dir':str(Path('outputs')/('experiment_'+experiment_id)),
        'created_at':datetime.now(timezone.utc).isoformat(), 'demo':True},
      'provenance':{
        'source':'executed_v2_algorithm_demo',
        'raw_result_sha256':hashlib.sha256(raw).hexdigest(),
        'full_result_artifact':'reports/execution_result.json.gz',
        'dataset':dataset,'dataset_selection':select,
        'uses_actual_labels':True,
        'note':'All metrics calculated by executed AAS detector; demonstration only'},
      'results':[{'detector':algorithm,'status':'success',
                   'metrics':metrics,'chart_trace':chart_trace(result)}]
    }
    if kind=='training':
        path=Path(model['file_path'])
        summary['training']={'algorithm':algorithm,'dataset':dataset,'table':'v2_channels',
            'model_dir':model['file_path'],'model_file_path':(path/'model.pkl').as_posix(),
            'model_name':model['model_name'],'model_published':True}
    else:
        summary['evaluation']={'model_id':model['id'],'model_name':model['model_name'],
            'model_algorithm':algorithm,'eval_dataset':dataset,'eval_table':'v2_channels'}
    (report_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return str((report_dir/'summary.json').relative_to(ROOT))


def run(*,seed_simulation=True):
    db=active_path(ROOT)
    if not db.is_file():raise FileNotFoundError('安装/重建带 targets 的 V2 数据库后才能生成实际任务记录')
    if seed_simulation: seed_default(ROOT)
    dataset=active_name()
    train=selection([6,9],200,420)
    test=selection([1,5],10000,10240)
    expected={'feature_keys':KEYS,'train_run_evidence':load_selection(db,selection=train,role='train')[1]['runs']}
    existing=[m for m in main.get_models(status='published',limit=500)+main.get_models(status='evaluated',limit=500)
              if m['model_name']=='示例 · 飞轮转速 Isolation Forest']
    if existing:
        return {'status':'exists','model_id':existing[0]['id'],'reports':[s['path'] for s in main.list_experiment_summaries() if s.get('experiment_id','').startswith('demo_')]}
    out=main.run_evaluation(dataset=dataset,table='v2_channels',algorithm='isolation_forest_detector',
       train_ratio=.75,algorithm_params={'n_estimators':20,'contamination':.05,'random_state':42},
       normalization_method='z_score',v2_run_selection=train,v2_role='train',
       v2_expected_sources=expected,alignment_method='exact',alignment_tolerance_seconds=0)
    mi=out['model_info']
    if not mi.get('model_dir'):raise RuntimeError('real model file missing')
    main.create_model(out['experiment_id'],'示例 · 飞轮转速 Isolation Forest',
       '以真实正常遥测训练、留出运行评估的教学演示模型',
       'isolation_forest_detector',dataset,'v2_channels',mi['model_dir'],out['result']['metrics'],out['config'])
    model=main.get_model_by_experiment_id(out['experiment_id'])
    if model is None:raise RuntimeError('model publication failed')
    # Config in the model registry is based on the actual execution, not UI defaults.
    train_report=_save_report('training',out['experiment_id'],model['algorithm'],model,dataset,
                              out['result'],train,out['result']['metrics'])
    scored=_evaluation(main,json.dumps({'model_id':model['id'],'dataset_selection':test}).encode())
    summary=scored['summary']
    eid='demo_eval_'+out['experiment_id']
    evaluation_report=_save_report('evaluation',eid,model['algorithm'],model,dataset,
                                   summary['result'],test,scored['metrics'])
    evidence={'status':'success','model_id':model['id'],'training_id':out['experiment_id'],
              'training_report':train_report,'evaluation_report':evaluation_report,
              'train_rows':222,'test_rows':242,'train':train,'test':test,
              'training_metrics':out['result']['metrics'],'evaluation_metrics':scored['metrics'],
              'simulated_database':'datasets/aas_simulation_v2.sqlite',
              'notes':'Detection quality is not asserted; these are reproducible demonstration results.'}
    (ROOT/'PHASE13_DEMO_RESULTS.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return evidence


def main_cli():
    parser=argparse.ArgumentParser()
    parser.add_argument('--skip-simulation',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(seed_simulation=not args.skip_simulation),ensure_ascii=False,indent=2))

if __name__=='__main__':main_cli()
