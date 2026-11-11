"""Dedicated HTTP POST dispatch for AAS, original endpoint contracts preserved."""
from __future__ import annotations

import base64
import binascii
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
import hashlib
from pathlib import Path
from urllib.parse import urlparse

from scripts.server.data_routes import POST_DATA_ROUTES
from scripts.server.v2_database_config import active_path
from .handler_runtime import main_from_compatibility_facade
from .post_models import handle_model_post
from .post_extensions import handle_extension_post

EXTENSION_POST_PATHS = frozenset({
    '/api/preview-algorithm', '/api/dependencies/install',
    '/api/install-dependency', '/api/install-all-dependencies',
    '/api/log-action', '/api/import-algorithm',
    '/api/plugins/native-build', '/api/plugins/preview',
    '/api/plugins/install', '/api/plugins/uninstall',
})

# Kept as explicit 410 contracts; the corresponding old handlers were unreachable.
RETIRED_POST_PATHS = frozenset({
    '/api/export-tables', '/api/export-logical-series',
    '/api/telemetry-partitions/refresh', '/api/fault-intervals',
    '/api/preview-import', '/api/sqlite-table/delete',
    '/api/import-data', '/api/import-data-multi',
    '/api/preprocessing/run', '/api/preprocessing/delete',
    '/api/run-experiment', '/api/save-experiment',
    '/api/experiments/publish-model',
    '/api/backup/create', '/api/backup/restore',
    '/api/backup/delete', '/api/backup/cleanup',
})

class PostRoutesMixin:
    def _dispatch_POST(self):
        parsed = urlparse(self.path)
        main = main_from_compatibility_facade(self)
        if main is None:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "主模块未加载"}).encode("utf-8"))
            return

        if parsed.path == '/_aas/control/shutdown':
            from scripts.server.local_service_control import authorized_shutdown_request
            supplied = self.headers.get('X-AAS-Control-Token', '')
            host = self.headers.get('Host', '')
            if self.headers.get('Content-Length', '0') not in ('', '0'):
                return main.json_response(self, {'error': '停止请求不接受请求体'}, 400)
            if not authorized_shutdown_request(
                    main.ROOT, os.getpid(), self.server.server_port,
                    self.client_address[0], host, supplied):
                return main.json_response(self, {'error': '本机服务控制凭据无效'}, 403)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return main.json_response(self, {'status': 'stopping'})

        if self._handle_auth_post(parsed, main):
            return
        if not self._require_auth(parsed, main, is_post=True):
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return main.json_response(self, {"error": "invalid Content-Length"}, 400)
        if parsed.path == '/api/task-cancel':
            if not 0 < length <= 4096:
                return main.json_response(self, {'error': '取消请求无效'}, 400)
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(payload, dict) or set(payload) != {'task_id', 'confirm'}:
                    raise ValueError('取消请求只接受 task_id 和 confirm')
                task_id = payload.get('task_id')
                if not isinstance(task_id, str) or not task_id or len(task_id) > 40:
                    raise ValueError('任务编号无效')
                if payload.get('confirm') is not True:
                    raise ValueError('取消任务必须明确确认 confirm=true')
                result = main.request_cancel_task(task_id)
                if result is None:
                    return main.json_response(self, {'error': '任务不存在'}, 404)
                return main.json_response(self, result, 202 if result.get('accepted') else 409)
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                return main.json_response(self, {'error': str(exc)}, 400)
        if parsed.path == '/api/v2/models/import-bundle':
            from scripts.server.v2_model_bundle import MAX_BUNDLE_BYTES, install_model_bundle
            if not 0 < length <= MAX_BUNDLE_BYTES:
                return main.json_response(self,{'error':'模型 ZIP 文件超过 75 MB 上传限制'},413)
            try:
                encoded=self.headers.get('X-AAS-Model-Name-B64','')
                if not 0 < len(encoded) <= 600:
                    raise ValueError('请输入导入后模型的名称')
                model_name=base64.b64decode(encoded,validate=True).decode('utf-8')
                content=self.rfile.read(length)
                if len(content)!=length:raise ValueError('模型上传数据不完整')
                result=install_model_bundle(main,content,model_name)
                return main.json_response(self,result)
            except (ValueError,TypeError,UnicodeError,binascii.Error,
                    FileNotFoundError,OSError,RuntimeError,KeyError,ImportError) as exc:
                return main.json_response(self,{'error':str(exc)},400)
        if parsed.path in ('/api/v2/telemetry/preprocess-csv/inspect',
                           '/api/v2/telemetry/preprocess-csv/process'):
            from scripts.server import v2_csv_preprocess as csv_preprocess
            if not 0 < length <= csv_preprocess.MAX_BYTES:
                return main.json_response(self, {'error': 'CSV 大小必须在 1 字节至 8 MB 以内'}, 413)
            try:
                encoded = self.headers.get('X-AAS-CSV-Filename-B64', '')
                if not 0 < len(encoded) <= 600:
                    raise ValueError('缺少 CSV 文件名')
                filename = base64.b64decode(encoded, validate=True).decode('utf-8')
                config_header = self.headers.get('X-AAS-Preprocess-Config-B64', '')
                if len(config_header) > 4096:
                    raise ValueError('预处理配置过大')
                config = (json.loads(base64.b64decode(config_header, validate=True).decode('utf-8'))
                          if config_header else {})
                if not isinstance(config, dict):
                    raise ValueError('预处理配置必须为对象')
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ValueError('CSV 上传不完整')
                if parsed.path.endswith('/inspect'):
                    result = csv_preprocess.inspect(content, filename,
                        encoding=config.get('encoding', 'auto'),
                        delimiter=config.get('delimiter', 'auto'))
                    return main.json_response(self, result)
                output, summary = csv_preprocess.process(content, filename, config)
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="aas-preprocessed.csv"')
                self.send_header('Content-Length', str(len(output)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-AAS-Preprocess-Rows', str(summary['input_rows']))
                self.end_headers()
                self.wfile.write(output)
                return
            except (ValueError, TypeError, UnicodeError, binascii.Error, KeyError, OSError) as exc:
                return main.json_response(self, {'error': str(exc)}, 400)
        if parsed.path in ('/api/v2/csv-workspace/inspect','/api/v2/csv-workspace/import'):
            from scripts.server import v2_csv_workspace as workspace
            if not 0 < length <= workspace.MAX_CSV_BYTES:
                return main.json_response(self, {'error':'CSV 大小必须在 1B～8MB 以内'},413)
            try:
                filename_encoded = self.headers.get('X-AAS-CSV-Filename-B64')
                filename = (base64.b64decode(filename_encoded,validate=True).decode('utf-8')
                            if filename_encoded else self.headers.get('X-AAS-CSV-Filename',''))
                format_header = self.headers.get('X-AAS-CSV-Format-B64', '')
                if len(format_header) > 2048:
                    raise ValueError('CSV 格式确认信息过大')
                format_meta = (json.loads(base64.b64decode(format_header, validate=True).decode('utf-8'))
                               if format_header else {})
                if not isinstance(format_meta, dict) or set(format_meta) - {
                        'csv_encoding', 'csv_delimiter', 'format_confirmed'}:
                    raise ValueError('CSV 格式确认信息包含不支持的字段')
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ValueError('上传文件未完整接收')
                if parsed.path.endswith('/inspect'):
                    result = workspace.inspect_csv(
                        content, filename,
                        csv_encoding=format_meta.get('csv_encoding', 'auto'),
                        csv_delimiter=format_meta.get('csv_delimiter', 'auto'))
                else:
                    result = workspace.import_csv(
                        main.ROOT, content, filename,
                        csv_encoding=format_meta.get('csv_encoding', 'auto'),
                        csv_delimiter=format_meta.get('csv_delimiter', 'auto'),
                        format_confirmed=format_meta.get('format_confirmed', False))
                return main.json_response(self,result)
            except (ValueError,TypeError,UnicodeError,binascii.Error,
                    sqlite3.DatabaseError,OSError) as exc:
                return main.json_response(self,{'error':str(exc)},400)
        if parsed.path in ('/api/v2/csv-workspace/fault-preview','/api/v2/csv-workspace/fault-save',
                           '/api/v2/csv-workspace/event-retract-preview',
                           '/api/v2/csv-workspace/event-retract',
                           '/api/v2/csv-workspace/rollback-preview',
                           '/api/v2/csv-workspace/rollback'):
            from scripts.server import v2_csv_workspace as workspace
            if not 0 < length <= 16_000:
                return main.json_response(self,{'error':'故障模拟配置超出允许范围'},413)
            try:
                request=json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(request,dict):
                    raise ValueError('故障模拟请求必须为 JSON 对象')
                if parsed.path.endswith('fault-preview'):
                    result = workspace.preview_fault(main.ROOT,request)
                elif parsed.path.endswith('fault-save'):
                    result = workspace.save_fault(main.ROOT,request)
                elif parsed.path.endswith('event-retract-preview'):
                    if set(request) != {'dataset_id', 'event_id'}:
                        raise ValueError('事件撤销预览只接受数据集 ID 和事件 ID')
                    result = workspace.preview_retract_event(
                        main.ROOT,request.get('dataset_id'),request.get('event_id'))
                elif parsed.path.endswith('event-retract'):
                    result = workspace.retract_event(main.ROOT,request)
                elif parsed.path.endswith('rollback-preview'):
                    if set(request) != {'dataset_id', 'ancestor_id'}:
                        raise ValueError('版本回退预览只接受当前版本 ID 和祖先版本 ID')
                    result = workspace.preview_rollback(
                        main.ROOT,request.get('dataset_id'),request.get('ancestor_id'))
                else:
                    result = workspace.rollback_to_ancestor(main.ROOT,request)
                return main.json_response(self,result)
            except (ValueError,TypeError,KeyError,UnicodeError,sqlite3.DatabaseError,OSError) as exc:
                return main.json_response(self,{'error':str(exc)},400)
        if parsed.path in ('/api/v2/telemetry/import-csv','/api/v2/telemetry/import-table') and length > 8_000_000:
            return main.json_response(self, {"error": "V2 CSV 上传请求超过 8MB，请按时间分批导入"}, 413)
        if parsed.path in ('/api/v2/telemetry/import-source', '/api/v2/user-csv-workspace/import-source'):
            workspace_scope = parsed.path == '/api/v2/user-csv-workspace/import-source'
            workspace_role = self.headers.get('X-AAS-CSV-Role', '') if workspace_scope else None
            if workspace_scope and workspace_role not in ('train', 'test'):
                return main.json_response(self, {'error': '用户 CSV 工作区必须明确指定 train 或 test 用途'}, 400)
            from scripts.server.telemetry_v2_source_import import (
                MAX_UPLOAD_BYTES, SourceImportCancelled, source_operation_file)
            from scripts.server.task_diagnostics import diagnose_failure
            if length == 0 or length > MAX_UPLOAD_BYTES:
                return main.json_response(self, {'error':'源文件应为 1B～64MB'},413)
            metadata_header = self.headers.get('X-AAS-Import-Metadata','')
            if len(metadata_header) > 32000:
                return main.json_response(self, {'error':'导入映射元数据过大'},413)
            upload_path = None
            task_id = None
            task_api = all(callable(getattr(main, name, None)) for name in (
                'create_task', 'bind_task_thread', 'update_task_progress',
                'is_cancel_requested', 'set_task_cancellable', 'complete_task'))
            try:
                metadata = json.loads(base64.b64decode(metadata_header,validate=True).decode('utf-8'))
                if not isinstance(metadata,dict):
                    raise ValueError('导入元数据必须是 JSON 对象')
                if workspace_scope and metadata.get('operation') in ('preview', 'commit'):
                    config = metadata.get('config')
                    if not isinstance(config, dict) or config.get('role') != workspace_role:
                        raise ValueError('CSV 用途与工作区入口不一致，拒绝导入')
                if workspace_scope:
                    from scripts.server import v2_user_csv_workspace as user_workspace
                    user_workspace.initialize(main.ROOT)
                if task_api:
                    task_id = uuid.uuid4().hex
                    main.create_task(task_id, 'csv_import', {})
                stage_dir = Path(main.ROOT) / 'outputs' / 'import_staging'
                stage_dir.mkdir(parents=True, exist_ok=True)
                # Clean only abandoned staging files older than one day. Active
                # uploads are newer and have unique task-derived names.
                cutoff = time.time() - 24 * 60 * 60
                for stale in stage_dir.glob('*.upload'):
                    try:
                        if stale.is_file() and not stale.is_symlink() and stale.stat().st_mtime < cutoff:
                            stale.unlink()
                    except OSError:
                        pass
                if task_id:
                    upload_path = stage_dir / f'{task_id}.upload'
                    handle = upload_path.open('xb')
                else:
                    fd, staged_name = tempfile.mkstemp(prefix='aas-csv-', suffix='.upload', dir=stage_dir)
                    upload_path = Path(staged_name)
                    handle = os.fdopen(fd, 'wb')
                digest = hashlib.sha256()
                received = 0
                last_progress = -1
                with handle:
                    while received < length:
                        chunk = self.rfile.read(min(256 * 1024, length - received))
                        if not chunk:
                            raise ValueError('源文件内容未完整接收')
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if task_id:
                            progress = min(10, 1 + int(9 * received / length))
                            if progress != last_progress:
                                main.update_task_progress(
                                    task_id, progress, f'已接收上传文件 {received}/{length} 字节', stage='csv_import')
                                last_progress = progress
                    handle.flush()
                    os.fsync(handle.fileno())

                if not task_api:
                    try:
                        if workspace_scope:
                            from scripts.server.v2_user_csv_adapter import inspect_or_preview, commit_user_csv
                            if metadata.get('operation') == 'commit':
                                result = commit_user_csv(main.ROOT, upload_path.read_bytes(), metadata.get('filename'),
                                    metadata, role=workspace_role)
                            else:
                                result = inspect_or_preview(main.ROOT, upload_path, metadata,
                                    source_digest=digest.hexdigest(), source_size=length, role=workspace_role)
                        else:
                            result = source_operation_file(
                                active_path(main.ROOT), upload_path, metadata,
                                source_digest=digest.hexdigest(), source_size=length)
                        return main.json_response(self, result)
                    finally:
                        upload_path.unlink(missing_ok=True)

                def work():
                    try:
                        main.bind_task_thread(task_id)
                        main.update_task_progress(task_id, 12, '文件已安全暂存，开始检查与解析', stage='csv_import')
                        if workspace_scope:
                            from scripts.server.v2_user_csv_adapter import inspect_or_preview, commit_user_csv
                            progress = lambda value, message, stage: main.update_task_progress(
                                task_id, value, message, stage=stage)
                            cancel = lambda: main.is_cancel_requested(task_id)
                            before_commit = lambda: main.set_task_cancellable(
                                task_id, False, reason='即将开始用户 CSV 工作区写入；数据库事务阶段不可中断')
                            if metadata.get('operation') == 'commit':
                                result = commit_user_csv(main.ROOT, upload_path.read_bytes(), metadata.get('filename'),
                                    metadata, role=workspace_role, progress=progress,
                                    cancel_requested=cancel, before_commit=before_commit,
                                    task_id=task_id, task_status_lookup=main.get_public_task_status)
                            else:
                                result = inspect_or_preview(main.ROOT, upload_path, metadata,
                                    source_digest=digest.hexdigest(), source_size=length, role=workspace_role,
                                    progress=progress, cancel_requested=cancel)
                        else:
                            result = source_operation_file(
                                active_path(main.ROOT), upload_path, metadata,
                                source_digest=digest.hexdigest(), source_size=length,
                                progress=lambda value, message, stage: main.update_task_progress(
                                    task_id, value, message, stage=stage),
                                cancel_requested=lambda: main.is_cancel_requested(task_id),
                                before_commit=lambda: main.set_task_cancellable(
                                    task_id, False, reason='即将开始数据库写入；样本写入与事务提交阶段不可中断'))
                        main.complete_task(task_id, result)
                    except SourceImportCancelled:
                        main.complete_task(task_id, None, cancelled=True)
                    except Exception as exc:
                        failure = diagnose_failure(exc, 'csv_import', 'csv_import')
                        main.complete_task(task_id, None, str(exc), failure=failure)
                    finally:
                        upload_path.unlink(missing_ok=True)

                try:
                    threading.Thread(target=work, daemon=True,
                                     name='aas-csv-import-' + task_id[:8]).start()
                except Exception as exc:
                    failure = diagnose_failure(exc, 'worker_start', 'csv_import')
                    main.complete_task(task_id, None, str(exc), failure=failure)
                    upload_path.unlink(missing_ok=True)
                    return main.json_response(self, {'error':'CSV 导入任务未能启动','task_id':task_id}, 503)
                return main.json_response(self, {'status':'accepted','task_id':task_id}, 202)
            except (ValueError,TypeError,UnicodeDecodeError,binascii.Error,sqlite3.DatabaseError,
                    FileNotFoundError,OSError,json.JSONDecodeError) as exc:
                if task_id and task_api:
                    failure = diagnose_failure(exc, 'csv_import', 'csv_import')
                    main.complete_task(task_id, None, str(exc), failure=failure)
                if upload_path is not None:
                    try:
                        upload_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return main.json_response(self,{'error':str(exc),'task_id':task_id},400)
        body = self.rfile.read(length) if length else b"{}"

        if parsed.path == '/api/v2/csv-workspace/delete':
            try:
                if length > 8000:
                    raise ValueError('删除请求参数过大')
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict) or set(payload) != {'dataset_id', 'confirm'} or payload.get('confirm') is not True:
                    raise ValueError('删除模拟 CSV 必须明确提交 dataset_id 和 confirm=true')
                from scripts.server import v2_csv_workspace as simulation_workspace
                result = simulation_workspace.delete_dataset(main.ROOT, payload['dataset_id'], confirm=True)
                return main.json_response(self, result)
            except KeyError as exc:
                return main.json_response(self, {'error':str(exc)},404)
            except (ValueError,TypeError,UnicodeDecodeError,sqlite3.DatabaseError,OSError,json.JSONDecodeError) as exc:
                return main.json_response(self, {'error':str(exc)},400)

        if parsed.path == '/api/v2/user-csv-workspace/delete':
            try:
                if length > 8000:
                    raise ValueError('删除请求参数过大')
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict) or set(payload) != {'dataset_id', 'confirm'} or payload.get('confirm') is not True:
                    raise ValueError('删除用户 CSV 必须明确提交 dataset_id 和 confirm=true')
                from scripts.server import v2_user_csv_workspace as user_workspace
                lookup = getattr(main, 'get_public_task_status', None)
                if callable(lookup):
                    user_workspace.reconcile_task_refs(main.ROOT, lookup)
                result = user_workspace.delete_dataset(main.ROOT, payload['dataset_id'], confirm=True)
                return main.json_response(self, result)
            except RuntimeError as exc:
                return main.json_response(self, {'error':str(exc)},409)
            except KeyError as exc:
                return main.json_response(self, {'error':str(exc)},404)
            except (ValueError,TypeError,UnicodeDecodeError,sqlite3.DatabaseError,OSError,json.JSONDecodeError) as exc:
                return main.json_response(self, {'error':str(exc)},400)

        if parsed.path in ('/api/v2/telemetry/batch/preview',
                           '/api/v2/telemetry/batch/start',
                           '/api/v2/telemetry/batch/retry'):
            from scripts.server import telemetry_v2_batch_tasks as batch
            try:
                if length>24000: raise ValueError('批量导入请求超过大小限制')
                payload=json.loads(body.decode('utf-8'))
                if not isinstance(payload,dict):raise ValueError('请求体必须是 JSON 对象')
                if parsed.path.endswith('/preview'):
                    result=batch.preview(main.ROOT,payload)
                else:
                    if set(payload)!={'task_id','confirm'} or payload.get('confirm') is not True:
                        raise ValueError('启动批量任务需要明确 confirm=true')
                    task_runtime = main if all(callable(getattr(main, name, None)) for name in (
                        'create_task', 'bind_task_thread', 'update_task_progress',
                        'is_cancel_requested', 'set_task_cancellable', 'complete_task')) else None
                    result=batch.start(main.ROOT,payload['task_id'],
                        retry=parsed.path.endswith('/retry'), task_runtime=task_runtime)
                return main.json_response(self,result,202 if parsed.path.endswith(('/start','/retry')) else 200)
            except (ValueError,TypeError,KeyError,UnicodeDecodeError,OSError,json.JSONDecodeError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path in ('/api/v2/simulation/dry-run','/api/v2/simulation/save','/api/v2/simulation/remove'):
            try:
                if length>16000:raise ValueError('故障模拟参数超过限制')
                from scripts.server.v2_simulation_injection import (
                    preview_v2_injection,save_v2_injection,delete_injection)
                config=json.loads(body.decode('utf-8'))
                if not isinstance(config,dict):raise ValueError('故障模拟参数不是对象')
                if parsed.path == '/api/v2/simulation/remove':
                    if set(config)!={'id'}:raise ValueError('删除请求只能提交记录 ID')
                    result=delete_injection(main.ROOT,int(config['id']))
                elif parsed.path == '/api/v2/simulation/dry-run':
                    result=preview_v2_injection(main.ROOT,config)
                else:
                    result=save_v2_injection(main.ROOT,config)
                return main.json_response(self,result)
            except (ValueError,TypeError,KeyError,FileNotFoundError,sqlite3.DatabaseError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == '/api/v2/simulation/inject':
            try:
                if length>16000:raise ValueError('fault injection config is too large')
                from scripts.server.v2_simulation_injection import inject_from_gzip
                config=json.loads(body.decode('utf-8'))
                if not isinstance(config,dict) or set(config)-{'token','fault_type','onset_s','magnitude','max_points','column','model'}:
                    raise ValueError('unrecognized injection configuration')
                result=inject_from_gzip(main.ROOT,**config)
                return main.json_response(self,result)
            except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path.startswith('/api/simulation/'):
            return main.json_response(self, {'error':'旧模拟数据库接口已停用；不允许重新创建物理表'},410)

        if parsed.path in ('/api/v2/training/start','/api/v2/evaluation/run',
                           '/api/v2/inference/run','/api/v2/inference/preview',
                           '/api/v2/run-dataset/preview','/api/v2/run-dataset/coverage'):
            from scripts.server.telemetry_v2_routes import handle_post
            return handle_post(self,parsed,main,body)

        if parsed.path in RETIRED_POST_PATHS:
            return main.json_response(self,{'error':'旧物理表数据管理接口已停用，请使用 V2 原生导入和逻辑参量访问'},410)

        # Database write/import/export endpoints retain their existing URL contracts.
        data_route = POST_DATA_ROUTES.get(parsed.path)
        if data_route is not None:
            data_route(self, parsed, main, body)
            return



        if parsed.path in ('/api/train-model', '/api/evaluate-model', '/api/run-evaluation'):
            return main.json_response(self, {'error': '旧物理表运行入口已停用，请使用 V2 逻辑参量训练/评估接口'}, 410)
        if parsed.path == "/api/models":
            return handle_model_post(self, parsed, main, body)
        if parsed.path in EXTENSION_POST_PATHS:
            return handle_extension_post(self, parsed, main, body)
        return main.json_response(self, {"error": "not found"}, 404)
