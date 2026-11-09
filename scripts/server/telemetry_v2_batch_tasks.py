"""Phase49: server-local, reviewed batch import tasks with durable progress.

The browser cannot supply arbitrary server file paths. Administrators opt in via
AAS_BATCH_IMPORT_ROOT; every selection is resolved inside that directory.
Tasks are file-atomic (via Phase48 import_file), restart-safe and never copy
run-level label truth into channel fault truth.
"""
from __future__ import annotations
from utils.path_compat import is_relative_to

from datetime import datetime, timezone
import csv
import gzip
import hashlib
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path
import re
import threading
from uuid import uuid4

from scripts.build_v2_batch_manifest import build_plan
from scripts.server.telemetry_v2_batch_import import import_file, MAX_FILES
from scripts.server.task_diagnostics import diagnose_failure
from scripts.server.v2_database_config import active_path

_MUTEX = threading.RLock()
_ACTIVE: set[str] = set()
_ID = re.compile(r'^[0-9a-f]{32}$')
_ROOT_VAR = 'AAS_BATCH_IMPORT_ROOT'


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _root():
    setting = os.environ.get(_ROOT_VAR, '').strip()
    if not setting:
        raise ValueError('管理员尚未配置 AAS_BATCH_IMPORT_ROOT；请设置服务端受管导入目录后重启 AAS')
    root = Path(setting).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('受管导入目录不存在，请检查 AAS_BATCH_IMPORT_ROOT')
    return root


def _safe(root, relative, *, directory=False):
    if not isinstance(relative, str) or len(relative) > 250 or '\x00' in relative or '\\' in relative:
        raise ValueError('文件/目录选择不合法')
    if relative in ('', '.'):
        if directory: return root
        raise ValueError('必须选择文件')
    path = Path(relative)
    if path.is_absolute() or any(part in ('..', '.') for part in path.parts):
        raise ValueError('只能选择受管目录内的相对路径')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('受管目录中不允许引用符号链接')
    output = current.resolve()
    if not is_relative_to(output, root):
        raise ValueError('路径超出受管目录')
    if directory and not output.is_dir() or not directory and not output.is_file():
        raise ValueError('所选文件或目录不存在')
    return output


def _jobs(root):
    base = Path(root).resolve() / 'outputs' / 'v2_batch_jobs'
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save(path, obj):
    # Atomic update: a crash cannot leave a half-written task JSON record.
    tmp = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with tmp.open('x', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path):
    # Windows file-sharing semantics: while another thread performs the atomic
    # os.replace() in _save(), a concurrent open() of the same record may be
    # denied with ERROR_ACCESS_DENIED (Errno 13). POSIX rename never conflicts
    # with readers, so this bounded retry only matters on Windows and never
    # changes record content.
    last_error = None
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    raise last_error


def _sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest()


def _record_path(root, task_id):
    if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
        raise ValueError('任务编号不合法')
    return _jobs(root) / (task_id + '.json')


def _plan_path(root, task_id):
    return _jobs(root) / (task_id + '.plan.json')


def workspace():
    if not os.environ.get(_ROOT_VAR, '').strip():
        return {'enabled':False, 'reason':'请管理员配置 AAS_BATCH_IMPORT_ROOT 并重启服务', 'folders':[], 'archives':[]}
    root = _root()
    # Directory choices are bounded: shallow traversal only, no symlinks.
    folders=['.']
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink() and not child.name.startswith('.'):
            folders.append(child.name)
            if len(folders)>=201: break
    archives=[]
    for folder in folders:
        path = _safe(root, folder, directory=True)
        for item in path.iterdir():
            if item.is_file() and not item.is_symlink() and item.suffix.lower()=='.zip':
                archives.append(str(item.relative_to(root)).replace('\\','/'))
                if len(archives)>=200: break
        if len(archives)>=200: break
    return {'enabled':True, 'folders':folders, 'archives':archives,
            'description':'仅显示 AAS_BATCH_IMPORT_ROOT 及其一级子目录；大文件始终从服务端磁盘读取'}


def _header_all(path):
    # Read only the header, never inflate a large CSV during plan generation.
    opener = gzip.open if path.name.lower().endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as f:
        line = f.readline(64 * 1024 + 1)
    if len(line)>64*1024: raise ValueError(f'{path.name} 的 CSV 表头过长')
    fields=next(csv.reader([line], strict=True), [])
    if len(set(fields))!=len(fields) or not (set(fields)&{'timestamp','time_s'}):
        raise ValueError(f'{path.name} 缺少时间列或有重复列名')
    return fields


def _header(path):
    return [x for x in _header_all(path) if x not in ('timestamp','time_s','label','fault_label')]


def preview(app_root, request):
    if not isinstance(request,dict) or set(request)-{'folder','targets_archive','model','unit_overrides','confirm_normal'}:
        raise ValueError('批量预检请求字段不合法')
    root = _root()
    folder_rel=request.get('folder','.')
    folder = _safe(root,folder_rel,directory=True)
    targets = _safe(root,request.get('targets_archive',''))
    if targets.suffix.lower()!='.zip':raise ValueError('标签必须是 ZIP 文件')
    model = request.get('model','')
    overrides=request.get('unit_overrides') or {}
    if not isinstance(overrides,dict) or len(overrides)>64:
        raise ValueError('单位覆写必须是参量→单位字典（最多 64 项）')
    confirm=request.get('confirm_normal',False)
    if type(confirm) is not bool:raise ValueError('正常训练确认必须为布尔值')
    plan,report=build_plan(folder,targets,model=model,unit_overrides=overrides)
    if not plan['files']:
        raise ValueError('没有匹配的可导入运行文件')
    if len(plan['files'])>MAX_FILES: raise ValueError('超过批量文件数量限制')
    catalog=json.loads((Path(__file__).resolve().parents[2]/'web/visualization/telemetry-v2-column-catalog.json').read_text(encoding='utf-8'))['items']
    missing=set();invalid=[];snapshots=[];unconfirmed_normal=0;present=set()
    for item in plan['files']:
        source=_safe(root,str((folder/item['path']).relative_to(root)).replace('\\','/'))
        fields=_header(source)
        if not 1<=len(fields)<=64: raise ValueError(f'{source.name} 需要 1～64 个参量列')
        present.update(fields)
        if (item['config']['role']=='train' and
                item['config']['import_context']['run_role']=='NORMAL' and
                not confirm):
            # Only an explicit checked training-file label column can bypass
            # the separate NORMAL-source human confirmation.
            if not {'label','fault_label'}.intersection(_header_all(source)):
                unconfirmed_normal+=1
        for col in fields:
            entry=catalog.get(col)
            if not entry or not all(entry.get(k) for k in ('subsystem','device','variable')):
                invalid.append(col)
            elif not (overrides.get(col) or entry.get('unit')):
                missing.add(col)
        snapshots.append({'relative_path':str(source.relative_to(root)).replace('\\','/'),
                          'size':source.stat().st_size,'mtime_ns':source.stat().st_mtime_ns})
        if item['config']['role']=='train' and item['config']['import_context']['run_role']=='NORMAL':
            item['config']['all_normal_confirmed']=confirm
    if invalid:raise ValueError('存在未收录的参量列，需要先完善标准列目录：'+', '.join(sorted(set(invalid))[:12]))
    if set(overrides)-present:
        raise ValueError('单位覆写中有不属于本批文件的参量：'+', '.join(sorted(set(overrides)-present)[:10]))
    if missing: return {'ready':False,'missing_units':sorted(missing), 'count':len(plan['files']),
                        'excluded_count':len(report['excluded_train_fault']),
                        'message':'请为缺少标准单位的参量逐项填写单位后重新预检；不得自动猜测'}
    if unconfirmed_normal:
        return {'ready':False,'missing_units':[], 'unconfirmed_normal_files':unconfirmed_normal,
                'count':len(plan['files']), 'message':'有无逐点标签的 NORMAL 训练文件；必须勾选人工核验确认后才能启动'}
    task_id=uuid4().hex
    source_sha=_sha(targets)
    data={'schema_version':1,'files':plan['files'], 'sources':snapshots,
          'folder':str(folder.relative_to(root)).replace('\\','/') or '.',
          'targets_archive':str(targets.relative_to(root)).replace('\\','/'),
          'targets_sha256':source_sha,
          'database':str(active_path(app_root)), 'created_utc':_utc()}
    record={'id':task_id,'status':'previewed','created_utc':_utc(),'updated_utc':_utc(),
            'files_total':len(plan['files']),'files_processed':0,'counts':{'imported':0,'skipped':0,'failed':0},
            'results':[], 'preview':{'excluded_train_fault':len(report['excluded_train_fault']),
                                     'unmatched_count':len(report['unmatched']),
                                     'normal_train_confirmed':confirm,
                                     'targets_archive_members':report['verified_targets_archive_members']}}
    with _MUTEX:
        _save(_plan_path(app_root,task_id),data)
        _save(_record_path(app_root,task_id),record)
    return {'ready':True,'task_id':task_id,'count':len(plan['files']),
            'excluded_count':record['preview']['excluded_train_fault'],
            'unmatched_count':record['preview']['unmatched_count'],
            'normal_train_confirmed':confirm,
            'items':[{'filename':f['path'],'split':f['config']['import_context']['dataset_split'],
                      'role':f['config']['import_context']['run_role']} for f in plan['files'][:100]],
            'message':'完成清单级预检；各文件的逐点数值和标签校验会在正式执行时逐文件进行'}


def _load_record(app_root, task_id):
    p=_record_path(app_root,task_id)
    if not p.is_file():raise ValueError('任务不存在')
    # Serialize record read+correction with the worker's _save() under the same
    # reentrant mutex; combined with the _read() retry this closes the Windows
    # concurrent open-vs-replace window (no POSIX behavior change).
    with _MUTEX:
        record=_read(p)
        if record['status']=='running' and task_id not in _ACTIVE:
            record['status']='interrupted'
            record['updated_utc']=_utc()
            _save(p,record)
    return record


def status(app_root,task_id):
    record=_load_record(app_root,task_id)
    # Keep HTTP polling bounded even for a 5,000-file batch. The durable
    # on-disk record still retains all per-file results for audit/retry.
    if len(record.get('results',[]))>100:
        record={**record,'results':record['results'][-100:],
                'results_truncated':True}
    return record


def history(app_root):
    folder=_jobs(app_root)
    out=[]
    for path in sorted(folder.glob('*.json'), key=lambda f:f.stat().st_mtime,reverse=True):
        if path.name.endswith('.plan.json'):continue
        try:
            rec=_load_record(app_root,path.stem)
            out.append({k:v for k,v in rec.items() if k!='results'})
        except (ValueError, OSError, KeyError, json.JSONDecodeError): continue
        if len(out)>=50:break
    return {'items':out}


def _worker(app_root, task_id, task_runtime=None):
    record_path = _record_path(app_root, task_id)
    cancelled = False
    try:
        if task_runtime is not None:
            task_runtime.bind_task_thread(task_id)
            task_runtime.update_task_progress(task_id, 1, '批量导入任务已启动', stage='batch_import')
        plan = _read(_plan_path(app_root, task_id))
        root = _root()
        for index, (item, snapshot) in enumerate(zip(plan['files'], plan['sources'])):
            if task_runtime is not None and task_runtime.is_cancel_requested(task_id):
                cancelled = True
                break
            try:
                source = _safe(root, snapshot['relative_path'])
                stat = source.stat()
                if (stat.st_size, stat.st_mtime_ns) != (snapshot['size'], snapshot['mtime_ns']):
                    raise ValueError('文件自预检后发生变化，拒绝未经确认的导入')
                if _sha(_safe(root, plan['targets_archive'])) != plan['targets_sha256']:
                    raise ValueError('标签归档已变更，必须重新预检')
                # import_file owns one SQLite transaction. Cancellation is only
                # observed before or after this call, never inside its commit.
                result = import_file(plan['database'], source, item['config'])
            except (ValueError, OSError, RuntimeError, TypeError, KeyError, sqlite3.Error,
                    csv.Error, UnicodeError, zipfile.BadZipFile) as exc:
                result = {'status': 'failed', 'filename': item['path'], 'error': str(exc)[:400]}
            with _MUTEX:
                rec = _read(record_path)
                rec['results'].append(result)
                rec['files_processed'] = index + 1
                rec['counts'][result['status']] += 1
                rec['updated_utc'] = _utc()
                _save(record_path, rec)
            if task_runtime is not None:
                total = max(1, int(rec.get('files_total') or 1))
                progress = min(95, int(5 + 90 * rec['files_processed'] / total))
                task_runtime.update_task_progress(
                    task_id, progress,
                    f"已处理 {rec['files_processed']}/{total} 个文件（成功 {rec['counts']['imported']}，跳过 {rec['counts']['skipped']}，失败 {rec['counts']['failed']}）",
                    stage='batch_import')
                if task_runtime.is_cancel_requested(task_id):
                    cancelled = True
                    break

        if task_runtime is not None and not cancelled:
            # Atomic boundary: if cancellation acquired the runtime lock first,
            # set_task_cancellable returns False and cancellation wins. Once
            # this succeeds, further cancellation requests are rejected.
            if not task_runtime.set_task_cancellable(
                    task_id, False, reason='所有批量文件已处理，正在完成任务记录'):
                cancelled = task_runtime.is_cancel_requested(task_id)

        with _MUTEX:
            rec = _read(record_path)
            if cancelled:
                rec['status'] = 'cancelled'
                rec['message'] = '用户取消批量导入；已提交文件保留，未启动的文件未处理'
            else:
                rec['status'] = 'complete' if rec['counts']['failed'] == 0 else 'partial_failure'
            rec['updated_utc'] = _utc()
            _save(record_path, rec)

        if task_runtime is not None:
            result = {
                'status': rec['status'], 'files_total': rec['files_total'],
                'files_processed': rec['files_processed'], 'counts': rec['counts'],
            }
            if cancelled:
                task_runtime.complete_task(task_id, None, cancelled=True)
            else:
                task_runtime.complete_task(task_id, result)
    except Exception as exc:
        with _MUTEX:
            try:
                rec = _read(record_path)
                if task_runtime is not None and task_runtime.is_cancel_requested(task_id):
                    rec['status'] = 'cancelled'
                    rec['message'] = '用户取消批量导入；已提交文件保留'
                else:
                    rec['status'] = 'failed'
                    rec['error'] = str(exc)[:300]
                rec['updated_utc'] = _utc()
                _save(record_path, rec)
            except (OSError, ValueError):
                pass
        if task_runtime is not None:
            if task_runtime.is_cancel_requested(task_id):
                task_runtime.complete_task(task_id, None, cancelled=True)
            else:
                task_runtime.complete_task(
                    task_id, None, str(exc),
                    failure=diagnose_failure(exc, 'batch_import', 'batch_import'))
    finally:
        with _MUTEX:
            _ACTIVE.discard(task_id)


def start(app_root, task_id, *, retry=False, task_runtime=None):
    root=_root()
    with _MUTEX:
        rec=_load_record(app_root,task_id)
        allowed=('partial_failure','interrupted','failed','cancelled') if retry else ('previewed',)
        if rec['status'] not in allowed:raise ValueError('当前任务状态不允许启动或重试')
        if _ACTIVE:raise ValueError('已有批量导入任务正在运行，请等待完成')
        plan=_read(_plan_path(app_root,task_id))
        folder=_safe(root,plan['folder'],directory=True)
        if plan['database']!=str(active_path(app_root)):
            raise ValueError('当前目标数据库已变更，请重新预检')
        if not Path(plan['database']).is_file():raise ValueError('目标 V2 数据库不存在')
        for item, snap in zip(plan['files'],plan['sources']):
            source=_safe(root,snap['relative_path'])
            if not is_relative_to(source, folder): raise ValueError('数据文件不在所选导入目录内')
        if retry:
            # Committed files are idempotent; restart from the same immutable
            # reviewed plan and store a new attempt for auditability.
            new_id=uuid4().hex
            _save(_plan_path(app_root,new_id),plan)
            rec={'id':new_id,'parent_id':task_id,'status':'previewed','created_utc':_utc(),
                 'updated_utc':_utc(),'files_total':len(plan['files']),'files_processed':0,
                 'counts':{'imported':0,'skipped':0,'failed':0},'results':[], 'preview':rec.get('preview',{})}
            _save(_record_path(app_root,new_id),rec)
            task_id=new_id
        if task_runtime is not None:
            task_runtime.create_task(task_id, 'batch_import', {})
        rec['status']='running';rec['updated_utc']=_utc()
        _save(_record_path(app_root,task_id),rec)
        _ACTIVE.add(task_id)
        worker=threading.Thread(target=_worker,args=(app_root,task_id,task_runtime),daemon=True,name='aas-batch-'+task_id[:8])
        try:worker.start()
        except BaseException as exc:
            _ACTIVE.discard(task_id)
            rec['status']='failed';rec['error']='batch worker could not start';_save(_record_path(app_root,task_id),rec)
            if task_runtime is not None:
                task_runtime.complete_task(task_id, None, str(exc),
                    failure=diagnose_failure(exc, 'worker_start', 'batch_import'))
            raise
        return {'task_id':task_id,'status':'running','files_total':rec['files_total']}
