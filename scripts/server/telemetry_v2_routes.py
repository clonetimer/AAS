"""V2 labeled run subset training/evaluation, without physical table selectors."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from scripts.server.v2_run_dataset import catalog, load_selection, validate_training_selection, preview_plot_series
from scripts.server.feature_schema import (INTEGRITY_BOUND, INTEGRITY_INVALID,
                                           INTEGRITY_LEGACY, verify_stored_feature_schema)
from scripts.server.v2_algorithm_parameters import validate_algorithm_configuration
from scripts.server.task_diagnostics import (
    TaskOperationError, archive_attention, diagnose_failure,
)

from scripts.server.v2_database_config import active_name, active_path


def _db(main):
    path=active_path(main.ROOT)
    if not path.is_file():raise FileNotFoundError('当前未连接可用的 V2 数据库，请在系统配置中检查数据库文件')
    return path


def _resolve_data_source(main, request, role: str):
    """Resolve a browser source selector to one of two server-owned V2 DBs.

    The browser can select history or a registered user CSV ID. It can never
    submit a filesystem path. User CSV roles and source digests are rechecked
    against the isolated workspace registry before every operation.
    """
    if role == 'score_only':
        role = 'test'
    if role not in ('train', 'test'):
        raise ValueError('数据用途必须为 train 或 test')
    selected = request.get('data_source') if isinstance(request, dict) else None
    if selected is None:
        selected = {'type': 'history'}
    if isinstance(selected, str):
        selected = {'type': selected}
    if not isinstance(selected, dict) or set(selected) - {'type', 'dataset_id'}:
        raise ValueError('数据来源选择无效')
    source_type = selected.get('type', 'history')
    if source_type == 'history':
        if selected.get('dataset_id'):
            raise ValueError('历史数据库来源不接受用户 CSV 数据集 ID')
        db = _db(main)
        return db, active_name(), {'type': 'history', 'database_ref': active_name()}, None
    if source_type != 'user_csv':
        raise ValueError('不支持的数据来源类型')
    dataset_id = selected.get('dataset_id')
    from scripts.server import v2_user_csv_workspace as user_workspace
    record = user_workspace.get_dataset(main.ROOT, dataset_id)
    if record.get('role') != role:
        raise ValueError('用户 CSV 的用途与当前操作不一致')
    if record.get('import_status') != 'imported' or type(record.get('source_id')) is not int:
        raise ValueError('用户 CSV 尚未完成 V2 导入，请重新检查或完成导入')
    db = user_workspace.database_path(main.ROOT)
    if not db.is_file() or db.is_symlink():
        raise FileNotFoundError('用户 CSV 工作区数据库不存在或路径无效')
    from scripts.server.telemetry_v2_access import open_database
    with open_database(db) as conn:
        source = conn.execute('SELECT source_sha256,source_bytes FROM v2_sources WHERE id=?',
                              (record['source_id'],)).fetchone()
        roles = {row[0] for row in conn.execute(
            'SELECT DISTINCT role FROM v2_segments WHERE source_id=?', (record['source_id'],))}
    if (not source or source['source_sha256'] != record['sha256'] or
            source['source_bytes'] != record['size_bytes'] or roles != {role}):
        raise ValueError('用户 CSV 的 V2 来源摘要或用途与持久化登记不一致')
    selection = request.get('dataset_selection') if isinstance(request, dict) else None
    if not isinstance(selection, dict) and isinstance(request, dict):
        selection = request
    if isinstance(selection, dict):
        ids = selection.get('source_ids')
        if ids != [record['source_id']]:
            raise ValueError('用户 CSV 工作区只能选择当前数据集对应的唯一运行')
    reference = user_workspace.database_reference()
    provenance = {'type': 'user_csv', 'database_ref': reference,
        'workspace_dataset_id': record['id'], 'filename': record['filename'],
        'source_sha256': record['sha256'], 'role': role}
    return db, reference, provenance, record


def run_catalog(main, query=None):
    query = query or {}
    source = (query.get('source') or ['history'])[0]
    if source == 'history':
        return catalog(_db(main))
    if source != 'user_csv':
        raise ValueError('不支持的数据目录来源')
    dataset_id = (query.get('dataset_id') or [''])[0]
    from scripts.server import v2_user_csv_workspace as user_workspace
    record = user_workspace.get_dataset(main.ROOT, dataset_id)
    if record.get('role') not in ('train', 'test'):
        raise ValueError('用户 CSV 用途无效')
    db, _, _, record = _resolve_data_source(main,
        {'data_source': {'type': 'user_csv', 'dataset_id': dataset_id},
         'dataset_selection': {'source_ids': [record['source_id']]}}, record['role'])
    result = catalog(db)
    source_id = record['source_id']
    runs = [item for item in result['runs'] if item['source_id'] == source_id]
    if len(runs) != 1:
        raise ValueError('用户 CSV 没有唯一、合规的 V2 运行来源；请检查标签、时间基准和数值列')
    run = runs[0]
    if run['data_role'] != record['role']:
        raise ValueError('用户 CSV V2 来源用途与登记用途不一致')
    features = result['run_features'].get(str(source_id), [])
    if not features:
        raise ValueError('用户 CSV 没有可用于算法的数值参量')
    return {**result, 'runs': runs, 'features': features,
        'run_features': {str(source_id): features},
        'default_train_ids': [source_id] if run['allowed_role'] == 'train' else [],
        'default_test_ids': [source_id] if run['allowed_role'] in ('test', 'score_only') else [],
        'data_source': {'type': 'user_csv', 'dataset_id': record['id'],
            'filename': record['filename'], 'role': record['role'],
            'sha256': record['sha256']}}


def v2_models(main):
    output=[]
    # An evaluated model remains eligible for another evaluation.
    candidates=main.get_models(status='published',limit=500)+main.get_models(status='evaluated',limit=500)
    # The stored algorithm id is an internal identifier (e.g. torch_like_usad);
    # never show it to users where a display label exists.
    try:
        from detectors.registry import get_available_algorithms
        labels={item['name']:(item.get('label') or item['name']) for item in get_available_algorithms()}
    except (ImportError,ValueError,OSError):
        labels={}
    for model in candidates:
        try:
            config=main.load_model_config(str(main.resolve_managed_model_dir(model['file_path'])))
            contract=(config.get('config') or {}).get('data_contract') or {}
            if contract.get('version')==7 and contract.get('v2_run_selection') and contract.get('v2_run_manifest'):
                # A tampered layout contract must stay visible as invalid instead
                # of silently disappearing from the list.
                try:
                    schema=verify_stored_feature_schema(contract)
                    integrity=INTEGRITY_BOUND if schema else INTEGRITY_LEGACY
                    digest=str(contract.get('schema_sha256') or '')
                except ValueError:
                    schema,integrity,digest=None,INTEGRITY_INVALID,''
                sel=contract['v2_run_selection']; output.append({**model,
                    'feature_keys':sel['feature_keys'], 'feature_count':len(sel['feature_keys']),
                    'train_source_ids':sel['source_ids'],
                    'train_groups':[r['group_id'] for r in contract['v2_run_manifest']['runs']],
                    'data_source_provenance':contract.get('data_source_provenance') or {'type':'history'},
                    'alignment_method':sel.get('method','exact'),
                    'tolerance_seconds':sel.get('tolerance_seconds',0),
                    'coordinate_contract':contract.get('coordinate_contract') or {'mode':'time'},
                    'feature_semantics':(schema or {}).get('semantics'),
                    'schema_sha256':digest[:12] if digest else None,
                    'schema_revision':(schema or {}).get('revision'),
                    'integrity':integrity,
                    'algorithm_label':labels.get(model['algorithm'],model['algorithm'])})
        except (ValueError,OSError,TypeError,KeyError):
            continue
    return {'models':output,'total':len(output)}


def _auto_archive(main, kind, receipt):
    """Persist a verified server-owned run; never trust browser result fields."""
    from scripts.server.v2_experiment_archive import archive_receipt
    try:
        info = archive_receipt(main.ROOT, main.EXECUTION_REGISTRY,
                               kind=kind, receipt=receipt)
        return {'archive_status': info['archive_status'],
                'archive_id': info['experiment_id'],
                'summary_path': info['summary_path']}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        logging.exception('V2 %s computed but report archival failed', kind)
        attention = archive_attention()
        return {'archive_status': 'failed', 'archive_error': attention['message'],
                'archive_attention': attention}


def _training(main, body):
    request=json.loads(body.decode('utf-8'))
    if not isinstance(request, dict):
        raise ValueError('训练请求必须为 JSON 对象')
    db, db_ref, data_source_provenance, user_dataset = _resolve_data_source(main, request, 'train')
    if not isinstance(request,dict) or not isinstance(request.get('algorithm'),str) or not request['algorithm']:
        raise ValueError('必须指定检测算法')
    request['algorithm_params']=validate_algorithm_configuration(request['algorithm'],request.get('algorithm_params',{}))
    validation_request={key:value for key,value in request.items() if key!='data_source'}
    preview=validate_training_selection(db,validation_request)
    if request['algorithm'] == 'torch_like_pca_spe':
        components=int(request['algorithm_params'].get('n_components',1))
        if len(preview['feature_keys']) <= components:
            raise ValueError('PCA-SPE 需要至少两个参量，且 n_components 必须小于所选参量数')
    if request['algorithm'] == 'torch_like_tranad':
        params=request['algorithm_params']
        d=int(params.get('d_model',32));heads=int(params.get('n_heads',4))
        if d % heads:
            raise ValueError('TranAD 要求 d_model 能被 n_heads 整除')
    sel=request['dataset_selection']
    # The loader canonicalises training order; persist that exact order so the
    # contract, the fitted matrix and every later evaluation agree.
    if isinstance(sel,dict) and preview.get('feature_keys'):
        sel['feature_keys']=list(preview['feature_keys'])
    task_id=uuid.uuid4().hex
    main.create_training_task(task_id,{**request,'data_source_provenance':data_source_provenance})
    if user_dataset is not None:
        from scripts.server import v2_user_csv_workspace as user_workspace
        try:
            user_workspace.bind_task(main.ROOT, task_id, user_dataset['id'])
        except Exception as exc:
            main.complete_task(task_id, None, str(exc), failure=diagnose_failure(exc, 'data_binding', 'training'))
            raise
    cancel_check=getattr(main,'is_cancel_requested',lambda _task_id: False)
    set_cancellable=getattr(main,'set_task_cancellable',None)
    def work():
        summary = None
        receipt_created = False
        stage = 'data_loading'
        try:
            main.bind_task_thread(task_id)
            main.update_task_progress(task_id,5,'按运行、时间和参量读取带标签数据',stage=stage)
            pre={'status':'complete','database':'datasets/'+db_ref,'scope':'v2_run_subsets',
                 'role':'train','sources':preview['runs']}
            expected_sources={'feature_keys':preview['feature_keys'],
                              'train_run_evidence':preview['runs'],
                              'coordinate_mode':preview.get('coordinate_mode','time'),
                              'feature_schema':preview.get('feature_schema'),
                              'data_source_provenance':data_source_provenance}
            stage = 'algorithm_execution'
            main.update_task_progress(task_id,15,'正在执行检测算法训练',stage=stage)
            supervisor=getattr(main,'ALGORITHM_SUPERVISOR',None)
            if supervisor is not None:
                summary=supervisor.run_training(
                    algorithm=request['algorithm'], algorithm_params=request['algorithm_params'],
                    selection=sel, train_ratio=request.get('train_ratio',0.8),
                    normalization_method=request.get('normalization_method','min_max'),
                    training_strategy=request.get('training_strategy','standard'),
                    expected=expected_sources, database=db_ref,
                    progress=lambda progress,message: main.update_task_progress(
                        task_id,progress,message,stage='algorithm_execution'),
                    cancel_requested=lambda: cancel_check(task_id))
            else:
                # Compatibility for external scripts that pass an unbound facade.
                # All HTTP-bound contexts use the isolated subprocess supervisor.
                if user_dataset is not None:
                    raise RuntimeError('用户 CSV 工作区训练必须使用隔离算法子进程，当前运行环境未提供 AlgorithmProcessService')
                summary=main.run_evaluation(dataset=db_ref,table='v2_channels',
                    algorithm=request['algorithm'],train_ratio=request.get('train_ratio',0.8),
                    algorithm_params=request['algorithm_params'],
                    normalization_method=request.get('normalization_method','min_max'),
                    training_strategy=request.get('training_strategy','standard'),
                    v2_run_selection=sel,v2_role='train',
                    v2_expected_sources=expected_sources,
                    alignment_method=sel.get('method','exact'),
                    alignment_tolerance_seconds=sel.get('tolerance_seconds',0),
                    task_id=task_id)
            from scripts.server.algorithm_process import AlgorithmCancelledError
            if cancel_check(task_id):
                raise AlgorithmCancelledError('用户取消训练')
            if callable(set_cancellable) and not set_cancellable(
                    task_id, False, reason='模型校验、来源记录和报告归档阶段不可中断'):
                raise AlgorithmCancelledError('用户取消训练')
            stage = 'model_validation'
            main.update_task_progress(task_id,85,'检查已保存的模型文件',stage=stage)
            model_info=summary.get('model_info') or {}
            if not summary.get('experiment_id') or not model_info.get('model_dir') or not model_info.get('model_file_path'):
                raise RuntimeError('模型训练未产生有效可加载的模型文件')
            # Check the committed artifact, not an intermediate training folder.
            from scripts.core.model_manager import validate_saved_model
            saved_dir = main.resolve_managed_model_dir(model_info['model_dir'])
            validate_saved_model(saved_dir, request['algorithm'], experiment_id=summary['experiment_id'])
            stage = 'provenance_capture'
            main.update_task_progress(task_id,92,'核验实验输入与数据来源',stage=stage)
            snapshot=summary.pop('_input_snapshot',None)
            receipt=main.EXECUTION_REGISTRY.issue(kind='training',config={'dataset':db_ref,'data_source_provenance':data_source_provenance,**request},
                result=summary.get('result',{}),identity={'experiment_id':summary['experiment_id'],
                 'model_dir':model_info['model_dir'],'model_file_path':model_info.get('model_file_path')},
                 root=main.ROOT,db_path=db,
                 model_dir=main.resolve_managed_model_dir(model_info['model_dir']),
                 pre_data=pre,input_evidence=summary.get('result',{}).get('input_evidence'),
                 input_snapshot=snapshot)
            receipt_created = True
            stage = 'report_archive'
            main.update_task_progress(task_id,97,'正在保存实验报告',stage=stage)
            archive=_auto_archive(main,'training',receipt)
            main.complete_task(task_id,{'status':'success' if archive['archive_status']=='complete' else 'partial',
                'experiment_id':summary['experiment_id'],
                'algorithm':request['algorithm'],'dataset':db_ref,'data_source_provenance':data_source_provenance,'table_name':'v2_channels',
                'model_dir':model_info['model_dir'],'model_file_path':model_info.get('model_file_path'),
                'metrics':summary.get('result',{}).get('metrics',{}),
                'config':summary.get('config',{}),'summary':summary,**archive})
        except Exception as exc:
            if not receipt_created:
                from scripts.server.training_reliability import cleanup_unpublished_artifact
                cleanup_unpublished_artifact(main, summary)
            from scripts.server.algorithm_process import AlgorithmCancelledError
            if isinstance(exc, AlgorithmCancelledError) or cancel_check(task_id):
                main.complete_task(task_id, None, cancelled=True)
            else:
                main.complete_task(task_id,None,str(exc),failure=diagnose_failure(exc,stage,'training'))
        finally:
            if user_dataset is not None:
                from scripts.server import v2_user_csv_workspace as user_workspace
                user_workspace.release_task(main.ROOT, task_id)
    try:
        threading.Thread(target=work,daemon=True).start()
    except Exception as exc:
        main.complete_task(task_id, None, f'任务工作线程启动失败: {exc}',
                           failure=diagnose_failure(exc,'worker_start','training'))
        if user_dataset is not None:
            from scripts.server import v2_user_csv_workspace as user_workspace
            user_workspace.release_task(main.ROOT, task_id)
        raise
    return {'status':'accepted','task_id':task_id,'selection':{'rows':preview['aligned_count'],
            'features':preview['feature_keys'],'normal':preview['normal_count'],'fault':preview['fault_count'],
            'sample_check':preview['sample_check']}}


def _task_progress(main, task_id, progress, message, stage):
    if task_id is not None:
        main.update_task_progress(task_id, progress, message, stage=stage)


def _task_cancel_requested(main, task_id):
    checker = getattr(main, 'is_cancel_requested', None)
    return bool(task_id is not None and callable(checker) and checker(task_id))


def _task_enter_finalization(main, task_id):
    if task_id is None:
        return
    from scripts.server.algorithm_process import AlgorithmCancelledError
    if _task_cancel_requested(main, task_id):
        raise AlgorithmCancelledError('用户取消任务')
    setter = getattr(main, 'set_task_cancellable', None)
    if callable(setter) and not setter(
            task_id, False, reason='来源凭据签发和实验报告归档阶段不可中断'):
        raise AlgorithmCancelledError('用户取消任务')


def _start_async_v2_task(main, task_type, body, operation):
    """Run V2 evaluation/inference under the durable TaskRuntime lifecycle."""
    request = json.loads(body.decode('utf-8'))
    _, _, data_source_provenance, user_dataset = _resolve_data_source(main, request, 'test')
    task_id = uuid.uuid4().hex
    main.create_task(task_id, task_type, {'data_source_provenance': data_source_provenance})
    if user_dataset is not None:
        from scripts.server import v2_user_csv_workspace as user_workspace
        try:
            user_workspace.bind_task(main.ROOT, task_id, user_dataset['id'])
        except Exception as exc:
            main.complete_task(task_id, None, str(exc), failure=diagnose_failure(exc, 'data_binding', task_type))
            raise
    initial_stage = 'score_only_validation' if task_type == 'score_only' else 'evaluation_validation'

    def work():
        try:
            main.bind_task_thread(task_id)
            main.update_task_progress(task_id, 2, '任务已启动，正在检查模型和数据选择', stage=initial_stage)
            result = operation(main, body, task_id=task_id)
            main.complete_task(task_id, result)
        except TaskOperationError as exc:
            from scripts.server.algorithm_process import AlgorithmCancelledError
            if isinstance(exc.cause, AlgorithmCancelledError) or _task_cancel_requested(main, task_id):
                main.complete_task(task_id, None, cancelled=True)
            else:
                failure = diagnose_failure(exc.cause, exc.stage, exc.task_type)
                main.complete_task(task_id, None, str(exc.cause), failure=failure)
        except Exception as exc:
            from scripts.server.algorithm_process import AlgorithmCancelledError
            if isinstance(exc, AlgorithmCancelledError) or _task_cancel_requested(main, task_id):
                main.complete_task(task_id, None, cancelled=True)
            else:
                failure = diagnose_failure(exc, initial_stage, task_type)
                main.complete_task(task_id, None, str(exc), failure=failure)
        finally:
            if user_dataset is not None:
                from scripts.server import v2_user_csv_workspace as user_workspace
                user_workspace.release_task(main.ROOT, task_id)

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception as exc:
        failure = diagnose_failure(exc, 'worker_start', task_type)
        main.complete_task(task_id, None, str(exc), failure=failure)
        if user_dataset is not None:
            from scripts.server import v2_user_csv_workspace as user_workspace
            user_workspace.release_task(main.ROOT, task_id)
    return {'status': 'accepted', 'task_id': task_id}


def _evaluation(main, body, task_id=None):
    stage = 'evaluation_validation'
    _task_progress(main, task_id, 5, '正在检查评估条件', stage)
    try:
        request=json.loads(body.decode('utf-8'))
        if not isinstance(request,dict) or set(request)-{'model_id','dataset_selection','label_target','data_source'}:
            raise ValueError('评估只接受模型 ID 与运行/参量/时间选择')
        from scripts.server.v2_label_targets import normalize_target
        target=normalize_target(request.get('label_target'))
        model_id=request.get('model_id')
        if type(model_id) is not int or model_id<=0:raise ValueError('model_id 无效')
        model=main.get_model(model_id)
        if model is None:raise ValueError('模型不存在')
        directory=main.resolve_managed_model_dir(model['file_path'])
        saved=main.load_model_config(str(directory))
        dc=(saved.get('config') or {}).get('data_contract') or {}
        if dc.get('version')!=7 or not dc.get('v2_run_selection') or not dc.get('v2_run_manifest'):
            raise ValueError('仅支持按运行/参量训练并发布的 V2 模型')
        # Legacy (R3.8) artifacts have no schema: they evaluate, but are reported as
        # unbound. Tampered ones fail here rather than scoring wrong columns.
        stored_schema=verify_stored_feature_schema(dc)
        integrity=INTEGRITY_BOUND if stored_schema else INTEGRITY_LEGACY
        old=dc['v2_run_selection']; sel=request.get('dataset_selection')
        if not isinstance(sel,dict):raise ValueError('缺少测试数据选择')
        if (sel.get('method','exact') != old.get('method','exact') or
                sel.get('tolerance_seconds',0) != old.get('tolerance_seconds',0)):
            raise ValueError('评估必须使用模型保存的时间对齐方式和容差')
        if sel.get('coordinate_mode','time') != (dc.get('coordinate_contract') or {}).get('mode','time'):
            raise ValueError('测试数据坐标模式与已发布模型不一致')
        db, db_ref, data_source_provenance, user_dataset = _resolve_data_source(main, request, 'test')
        train_provenance = dc.get('data_source_provenance') or {'type':'history','database_ref':model.get('dataset') or active_name()}
        same_database = train_provenance.get('database_ref') == db_ref
        expected={'feature_keys':old['feature_keys'],
                  'coordinate_mode':(dc.get('coordinate_contract') or {}).get('mode','time'),
                  'train_source_ids':old['source_ids'] if same_database else [],
                  'train_source_digests':[r.get('source_sha256') for r in dc['v2_run_manifest']['runs'] if r.get('source_sha256')],
                  'train_groups':[r['group_id'] for r in dc['v2_run_manifest']['runs']],
                  'feature_schema':stored_schema}
        _,meta=load_selection(db,selection=sel,role='test',expected=expected,label_target=target)
        _task_progress(main, task_id, 22, '测试数据已就绪，正在执行评估', 'evaluation_execution')
        scale=dc.get('model_input_scaling')
        if not isinstance(scale,dict) or not scale.get('enabled'):
            raise ValueError('模型缺少训练期归一化契约，禁止测试数据拟合')
        pre={'status':'complete','database':'datasets/'+db_ref,'scope':'v2_run_subsets',
             'role':'test','sources':meta['runs']}

        stage = 'evaluation_execution'
        supervisor=getattr(main,'ALGORITHM_SUPERVISOR',None)
        if supervisor is not None:
            summary=supervisor.run_evaluation(
                algorithm=model['algorithm'], selection=sel, expected=expected,
                scaling=scale, feature_columns=dc['feature_columns'],
                model_dir=directory, database=db_ref,label_target=target,
                progress=(lambda value, message: _task_progress(
                    main, task_id, value, message, 'evaluation_execution')) if task_id else None,
                cancel_requested=(lambda: _task_cancel_requested(main, task_id)) if task_id else None)
        else:
            if user_dataset is not None:
                raise RuntimeError('用户 CSV 工作区评估必须使用隔离算法子进程，当前运行环境未提供 AlgorithmProcessService')
            detector=main.load_model(str(directory),model['algorithm'])
            if detector is None or not callable(getattr(detector,'score',None)):
                raise ValueError('模型不可评分，禁止测试时重新训练')
            summary=main.run_evaluation(dataset=db_ref,table='v2_channels',algorithm=model['algorithm'],
                train_ratio=1.0,preloaded_detector=detector,persist_model=False,
                input_scaling_contract=scale,feature_columns=dc['feature_columns'],
                v2_run_selection=sel,v2_role='test',v2_expected_sources=expected,
                v2_label_target=target,
                alignment_method=sel.get('method','exact'),
                alignment_tolerance_seconds=sel.get('tolerance_seconds',0))

        _task_enter_finalization(main, task_id)
        stage = 'provenance_capture'
        _task_progress(main, task_id, 90, '正在核验实验输入和数据来源', stage)
        snapshot=summary.pop('_input_snapshot',None)
        main.update_model_evaluation(model_id)
        receipt=main.EXECUTION_REGISTRY.issue(kind='evaluation',
            config={'dataset':db_ref,'data_source_provenance':data_source_provenance,**request},result=summary.get('result',{}),
            identity={'model_id':model_id,'model_name':model.get('model_name'),
              'model_algorithm':model['algorithm']},root=main.ROOT,db_path=db,
            model_dir=directory,pre_data=pre,
            input_evidence=summary.get('result',{}).get('input_evidence'),input_snapshot=snapshot)

        stage = 'report_archive'
        _task_progress(main, task_id, 97, '正在归档评估报告', stage)
        archive=_auto_archive(main,'evaluation',receipt)
        return {'status':'success' if archive['archive_status']=='complete' else 'partial',
                'model_id':model_id,'data_source_provenance':data_source_provenance,**archive,
                'metrics':summary.get('result',{}).get('metrics',{}),
                'input_contract':{'feature_keys':old['feature_keys'],'source_runs':meta['runs'],
                   'time_range':meta['time_window'],'alignment':meta['method'],
                   'source':'v2_labeled_run_subset','sample_check':meta['sample_check'],
                   'label_target':meta['label_target'],'label_coverage':meta['label_coverage'],
                   'feature_semantics':(stored_schema or {}).get('semantics'),
                   'schema_sha256':dc.get('schema_sha256') if stored_schema else None,
                   'integrity':integrity},'summary':summary}
    except TaskOperationError:
        raise
    except Exception as exc:
        from scripts.server.algorithm_process import AlgorithmCancelledError
        if isinstance(exc, AlgorithmCancelledError):
            raise
        raise TaskOperationError(stage, exc, 'evaluation') from exc


def _score_only_context(main, request):
    """Bind an unknown-label test run to one published model and its saved schema."""
    if not isinstance(request,dict) or set(request)-{'model_id','dataset_selection','data_source'}:
        raise ValueError('仅推理只接受模型 ID 与测试 CSV 运行/参量/时间选择')
    model_id=request.get('model_id')
    if type(model_id) is not int or model_id<=0:
        raise ValueError('model_id 无效')
    model=main.get_model(model_id)
    if model is None or model.get('status') not in ('published','evaluated'):
        raise ValueError('仅推理需要已发布、可复用的模型')
    directory=main.resolve_managed_model_dir(model['file_path'])
    saved=main.load_model_config(str(directory))
    dc=(saved.get('config') or {}).get('data_contract') or {}
    if dc.get('version')!=7 or not dc.get('v2_run_selection') or not dc.get('v2_run_manifest'):
        raise ValueError('仅推理只支持已发布的 V2 运行级模型')
    stored_schema=verify_stored_feature_schema(dc)
    if not stored_schema:
        raise ValueError('旧模型缺少绑定的特征物理语义契约，请重新训练并发布')
    old=dc['v2_run_selection']
    sel=request.get('dataset_selection')
    if not isinstance(sel,dict):
        raise ValueError('缺少测试 CSV 运行选择')
    if (sel.get('method','exact')!=old.get('method','exact') or
            sel.get('tolerance_seconds',0)!=old.get('tolerance_seconds',0)):
        raise ValueError('仅推理必须沿用模型训练期的时间对齐方式和容差')
    if sel.get('coordinate_mode','time') != (dc.get('coordinate_contract') or {}).get('mode','time'):
        raise ValueError('仅推理数据坐标模式与模型不兼容')
    scale=dc.get('model_input_scaling')
    if not isinstance(scale,dict) or scale.get('enabled') is not True:
        raise ValueError('模型缺少训练期归一化契约，禁止测试数据拟合')
    manifest=dc['v2_run_manifest']['runs']
    if not old.get('source_ids') or not manifest or any(
            not isinstance(run.get('source_sha256'),str) or len(run['source_sha256'])!=64
            for run in manifest):
        raise ValueError('模型缺少可核验的训练来源摘要，无法排除 CSV 训练/测试泄漏')
    db, db_ref, data_source_provenance, user_dataset = _resolve_data_source(main, request, 'test')
    train_provenance = dc.get('data_source_provenance') or {'type':'history','database_ref':model.get('dataset') or active_name()}
    same_database = train_provenance.get('database_ref') == db_ref
    expected={'feature_keys':old['feature_keys'],
              'coordinate_mode':(dc.get('coordinate_contract') or {}).get('mode','time'),
              'train_source_ids':old['source_ids'] if same_database else [],
              'train_groups':[run['group_id'] for run in manifest],
              'train_source_digests':[run.get('source_sha256') for run in manifest],
              'feature_schema':stored_schema}
    from scripts.server.v2_score_only import load_score_only_rows
    rows,meta=load_score_only_rows(db,selection=sel,expected=expected)
    return model_id,model,directory,dc,sel,scale,expected,db,db_ref,data_source_provenance,user_dataset,rows,meta


def _inference(main, body, task_id=None):
    stage = 'score_only_validation'
    _task_progress(main, task_id, 5, '正在检查仅推理条件', stage)
    try:
        request=json.loads(body.decode('utf-8'))
        (model_id,model,directory,dc,sel,scale,expected,db,db_ref,data_source_provenance,user_dataset,_,meta)=_score_only_context(main,request)
        _task_progress(main, task_id, 22, '测试数据已就绪，正在执行无标签推理', 'score_only_execution')
        pre={'status':'unavailable','reason':'selected_v2_sample_digests_not_physical_table_scan',
             'database':'datasets/'+db_ref,'scope':'v2_unlabeled_import_inference',
             'sources':meta['runs']}

        stage = 'score_only_execution'
        supervisor=getattr(main,'ALGORITHM_SUPERVISOR',None)
        if supervisor is not None:
            summary=supervisor.run_score_only(algorithm=model['algorithm'],selection=sel,
                expected=expected,scaling=scale,feature_columns=dc['feature_columns'],
                model_dir=directory,database=db_ref,
                progress=(lambda value, message: _task_progress(
                    main, task_id, value, message, 'score_only_execution')) if task_id else None,
                cancel_requested=(lambda: _task_cancel_requested(main, task_id)) if task_id else None)
        else:
            if user_dataset is not None:
                raise RuntimeError('用户 CSV 工作区仅推理必须使用隔离算法子进程')
            from scripts.server.v2_score_only import score_with_published_detector
            detector=main.load_model(str(directory),model['algorithm'])
            if detector is None or not callable(getattr(detector,'score',None)):
                raise ValueError('模型不可评分，禁止测试时重新训练')
            summary=score_with_published_detector(db,selection=sel,expected=expected,
                scaling=scale,feature_columns=dc['feature_columns'],
                algorithm=model['algorithm'],detector=detector)
        if (summary.get('evaluation_mode')!='score_only' or
                summary.get('ground_truth_available') is not False or
                summary.get('result',{}).get('metrics')!={}):
            raise RuntimeError('仅推理结果契约无效，禁止将未知真值作为监督评估归档')
        actual=(summary.get('input_contract') or {}).get('runs') or []
        if actual!=meta['runs']:
            raise ValueError('执行期间测试数据发生变化，请重新选择并运行')

        _task_enter_finalization(main, task_id)
        stage = 'provenance_capture'
        _task_progress(main, task_id, 90, '正在核验仅推理输入来源', stage)
        snapshot=summary.pop('input_snapshot',None)
        receipt=main.EXECUTION_REGISTRY.issue(kind='evaluation',
            config={'dataset':db_ref,'data_source_provenance':data_source_provenance,'evaluation_mode':'score_only',**request},
            result=summary['result'],
            identity={'model_id':model_id,'model_name':model.get('model_name'),
                      'model_algorithm':model['algorithm']},
            root=main.ROOT,db_path=db,model_dir=directory,pre_data=pre,
            input_evidence=summary['result'].get('input_evidence'),input_snapshot=snapshot)

        stage = 'report_archive'
        _task_progress(main, task_id, 97, '正在归档仅推理报告', stage)
        archive=_auto_archive(main,'evaluation',receipt)
        return {'status':'success' if archive['archive_status']=='complete' else 'partial',
                'model_id':model_id,'evaluation_mode':'score_only',
                'data_source_provenance':data_source_provenance,
                'ground_truth_available':False,'metrics':{},
                'input_contract':{**meta,'source':'v2_unlabeled_csv',
                    'feature_semantics':expected['feature_schema']['semantics'],
                    'schema_sha256':dc.get('schema_sha256'),'integrity':INTEGRITY_BOUND},
                'summary':summary,**archive}
    except TaskOperationError:
        raise
    except Exception as exc:
        from scripts.server.algorithm_process import AlgorithmCancelledError
        if isinstance(exc, AlgorithmCancelledError):
            raise
        raise TaskOperationError(stage, exc, 'score_only') from exc


def _inference_preview(main, body):
    request=json.loads(body.decode('utf-8'))
    _,_,_,_,_,_,_,_,_,_,_,rows,meta=_score_only_context(main,request)
    return {'role':'score_only','evaluation_mode':'score_only',
            'ground_truth_available':False,'feature_keys':meta['feature_keys'],
            'aligned_count':meta['aligned_count'],'runs':meta['runs'],
            'time_window':meta['time_window'],'sample_check':meta['sample_check'],
            'label_coverage':meta['label_coverage'],
            'plot_series':preview_plot_series(rows,meta),'plot_limit':1600}


def _record_sync_diagnostic(main, task_type, status, stage, message,
                            failure=None, attention=None, result=None):
    """Best-effort durable history for synchronous V2 eval/inference outcomes."""
    recorder = getattr(main, 'record_terminal_task', None)
    if not callable(recorder):
        return None
    try:
        return recorder(task_type=task_type, status=status, stage=stage,
                        message=message, failure=failure,
                        attention=attention, result=result)
    except Exception:
        logging.exception('Could not persist synchronous V2 task diagnostic')
        return None


def handle_post(handler,parsed,main,body):
    try:
        if len(body)>64_000:raise ValueError('请求体过大')
        if parsed.path=='/api/v2/run-dataset/coverage':
            from scripts.server.v2_run_dataset import selection_coverage
            request=json.loads(body.decode('utf-8'))
            db, _, _, _ = _resolve_data_source(main, request, request.get('role'))
            result=selection_coverage(db, role=request.get('role'),
                                      source_ids=request.get('source_ids'),
                                      feature_keys=request.get('feature_keys'),
                                      feature_semantics=request.get('feature_semantics'),
                                      coordinate_mode=request.get('coordinate_mode','time'),
                                      frame_sequence_id=request.get('frame_sequence_id'))
        elif parsed.path=='/api/v2/run-dataset/preview':
            request=json.loads(body.decode('utf-8'))
            role=request.get('role')
            from scripts.server.v2_label_targets import normalize_target
            target=normalize_target(request.get('label_target'))
            db, _, _, _ = _resolve_data_source(main, request, role)
            rows,meta=load_selection(db,selection=request.get('dataset_selection'),role=role,label_target=target)
            result={'role':role,'feature_keys':meta['feature_keys'],
                'aligned_count':meta['aligned_count'],'normal_count':meta['normal_count'],
                'fault_count':meta['fault_count'],'runs':meta['runs'],'time_window':meta['time_window'],
                'sample_check':meta['sample_check'],
                'label_target':meta['label_target'],'label_coverage':meta['label_coverage'],
                'plot_series':preview_plot_series(rows,meta),'plot_limit':1600}
        elif parsed.path=='/api/v2/training/start':result=_training(main,body)
        elif parsed.path=='/api/v2/inference/run':
            result=(_start_async_v2_task(main,'score_only',body,_inference)
                    if callable(getattr(main,'create_task',None)) else _inference(main,body))
        elif parsed.path=='/api/v2/inference/preview':result=_inference_preview(main,body)
        elif parsed.path=='/api/v2/evaluation/run':
            result=(_start_async_v2_task(main,'evaluation',body,_evaluation)
                    if callable(getattr(main,'create_task',None)) else _evaluation(main,body))
        else:raise ValueError('未知的运行级操作')
        if parsed.path in ('/api/v2/evaluation/run', '/api/v2/inference/run') and result.get('archive_attention'):
            task_type = 'score_only' if parsed.path == '/api/v2/inference/run' else 'evaluation'
            task_id = _record_sync_diagnostic(main, task_type, 'completed', 'report_archive',
                '计算完成，但实验报告需要处理。', attention=result['archive_attention'], result=result)
            if task_id:
                result['task_id'] = task_id
        return main.json_response(handler,result,202 if result.get('status')=='accepted' else 200)
    except TaskOperationError as exc:
        failure = diagnose_failure(exc.cause, exc.stage, exc.task_type)
        payload = {'error': failure['message'], 'failure': failure}
        if parsed.path in ('/api/v2/evaluation/run', '/api/v2/inference/run'):
            task_id = _record_sync_diagnostic(main, exc.task_type, 'failed', failure['stage'],
                failure['message'], failure=failure)
            if task_id:
                payload['task_id'] = task_id
        return main.json_response(handler, payload, 400)
    except Exception as exc:
        stage_by_path = {
            '/api/v2/training/start': 'preflight',
            '/api/v2/run-dataset/coverage': 'preflight',
            '/api/v2/run-dataset/preview': 'preflight',
            '/api/v2/inference/run': 'score_only_validation',
            '/api/v2/inference/preview': 'score_only_validation',
            '/api/v2/evaluation/run': 'evaluation_validation',
        }
        operation = 'score_only' if 'inference' in parsed.path else (
            'evaluation' if 'evaluation' in parsed.path else 'training')
        failure = diagnose_failure(exc, stage_by_path.get(parsed.path, 'preflight'), operation)
        payload = {'error': failure['message'], 'failure': failure}
        if parsed.path in ('/api/v2/evaluation/run', '/api/v2/inference/run'):
            task_id = _record_sync_diagnostic(main, operation, 'failed', failure['stage'],
                failure['message'], failure=failure)
            if task_id:
                payload['task_id'] = task_id
        return main.json_response(handler, payload, 400)
