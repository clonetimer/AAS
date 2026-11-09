"""Dedicated HTTP GET dispatch for AAS, original endpoint contracts preserved."""
from __future__ import annotations
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import sys
import threading
import uuid
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from scripts.server.data_routes import GET_DATA_ROUTES, POST_DATA_ROUTES
from scripts.server.v2_database_config import active_name, active_path
from scripts.server.home_auth import AuthError, auth_cookie, expired_cookie, same_origin
from scripts.server.model_source_contract import model_input_sources
from scripts.server.result_traces import chart_trace
from scripts.server.execution_provenance import data_fingerprint
from scripts.server.event_scoring import enrich_trace, load_events, source_tables as event_source_tables

from .handler_runtime import main_from_compatibility_facade
from .handler_plugins import collect_plugins, install_plugin, preview_plugin, uninstall_plugin

class GetRoutesMixin:
    def _dispatch_GET(self):
        parsed = urlparse(self.path)
        main = main_from_compatibility_facade(self)
        if main is None:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "主模块未加载"}).encode("utf-8"))
            return

        if parsed.path == '/api/auth/status':
            username = self.authenticated_user(main)
            return self._send_auth_response({
                'initialized': main.HOME_AUTH.initialized(),
                'authenticated': bool(username), 'username': username,
            })

        if parsed.path in ('/', '/home.html', '/home.css', '/home.js'):
            if parsed.path == '/':
                self.path = '/home.html'  # pylint: disable=attribute-defined-outside-init
            return super().do_GET()

        if not self._require_auth(parsed, main):
            return

        if parsed.path == '/api/v2/models/export-bundle':
            from scripts.server.v2_model_bundle import export_model_bundle
            try:
                model_id=int(parse_qs(parsed.query).get('model_id',['0'])[0])
                if model_id<=0:raise ValueError('模型 ID 无效')
                model=main.get_model(model_id)
                if not model:raise ValueError('未找到已注册模型')
                content=export_model_bundle(main.ROOT,model)
                self.send_response(200)
                self.send_header('Content-Type','application/zip')
                self.send_header('Content-Disposition',
                                 f'attachment; filename="aas-model-{model_id}.aas.zip"')
                self.send_header('Content-Length',str(len(content)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(content)
                return
            except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path in ('/api/v2/csv-workspace/list',
                           '/api/v2/csv-workspace/dataset-preview',
                           '/api/v2/csv-workspace/export',
                           '/api/v2/csv-workspace/events',
                           '/api/v2/csv-workspace/ancestors',
                           '/api/v2/csv-workspace/compare'):
            from scripts.server import v2_csv_workspace as workspace
            params = parse_qs(parsed.query)
            try:
                if parsed.path.endswith('/list'):
                    return main.json_response(self,workspace.list_datasets(main.ROOT))
                dataset_id=params.get('id',[''])[0]
                if parsed.path.endswith('/events'):
                    return main.json_response(self,workspace.event_timeline(main.ROOT,dataset_id))
                if parsed.path.endswith('/ancestors'):
                    return main.json_response(self,workspace.list_ancestors(main.ROOT,dataset_id))
                if parsed.path.endswith('/compare'):
                    before_id=params.get('before_id',[''])[0]
                    after_id=params.get('after_id',[''])[0]
                    selected=params.get('column',[None])[0]
                    return main.json_response(self,workspace.compare_versions(
                        main.ROOT,before_id,after_id,column=selected))
                if parsed.path.endswith('/dataset-preview'):
                    selected=params.get('column',[None])[0]
                    return main.json_response(self,workspace.dataset_preview(main.ROOT,dataset_id,selected))
                safe=params.get('raw',['0'])[0]!='1'
                content=workspace.export_csv(main.ROOT,dataset_id,spreadsheet_safe=safe)
                version=workspace.get_dataset(main.ROOT,dataset_id,include_frames=False)['version']
                self.send_response(200)
                self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition',
                                 f'attachment; filename="aas-simulation-{dataset_id}-v{version}.csv"')
                self.send_header('Content-Length',str(len(content)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                self.wfile.write(content)
                return
            except (ValueError,TypeError,KeyError,sqlite3.DatabaseError,OSError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path in ('/backup.html',):
            self.send_response(302)
            self.send_header('Location', '/browser.html')
            self.end_headers()
            return

        if parsed.path in ('/api/v2/telemetry/batch/workspace',
                           '/api/v2/telemetry/batch/history',
                           '/api/v2/telemetry/batch/status'):
            from scripts.server import telemetry_v2_batch_tasks as batch
            try:
                if parsed.path.endswith('/workspace'):
                    result=batch.workspace()
                elif parsed.path.endswith('/history'):
                    result=batch.history(main.ROOT)
                else:
                    task_id=parse_qs(parsed.query).get('task_id',[''])[0]
                    result=batch.status(main.ROOT,task_id)
                return main.json_response(self,result)
            except (ValueError,OSError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == '/api/v2/simulation/fault-types':
            from scripts.server.v2_simulation_injection import v2_fault_type_catalog
            return main.json_response(self, {'items':v2_fault_type_catalog()})

        if parsed.path == '/api/v2/simulation/sources':
            try:
                from scripts.server.v2_simulation_injection import v2_source_catalog
                return main.json_response(self,v2_source_catalog(main.ROOT))
            except (ValueError,FileNotFoundError,sqlite3.DatabaseError) as exc:
                return main.json_response(self,{'error':str(exc),'items':[]},400)

        if parsed.path == '/api/v2/simulation/export':
            try:
                from scripts.server.v2_simulation_injection import export_injection_csv
                qs=parse_qs(parsed.query)
                record_id=int(qs.get('id',['0'])[0])
                content=export_injection_csv(main.ROOT,record_id,
                    spreadsheet_safe=qs.get('raw',['0'])[0]!='1')
                self.send_response(200)
                self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition',
                                 f'attachment; filename="aas-v2-injection-{record_id}.csv"')
                self.send_header('Content-Length',str(len(content)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(content)
                return
            except (ValueError,TypeError,KeyError,sqlite3.DatabaseError,OSError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == '/api/v2/simulation/preview':
            try:
                from scripts.server.v2_simulation_injection import preview_injection
                qs=parse_qs(parsed.query)
                return main.json_response(self,preview_injection(main.ROOT,int(qs.get('id',['0'])[0])))
            except (ValueError,TypeError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == '/api/v2/simulation/source-time-bounds':
            try:
                from scripts.server.v2_simulation_injection import v2_injection_time_bounds
                qs=parse_qs(parsed.query)
                return main.json_response(self,v2_injection_time_bounds(main.ROOT,
                    source_id=int(qs.get('source_id',['0'])[0]),
                    channel_id=int(qs.get('channel_id',['0'])[0]),
                    max_points=int(qs.get('max_points',['0'])[0])))
            except (ValueError,TypeError,sqlite3.DatabaseError,FileNotFoundError) as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == '/api/v2/simulation/list':
            from scripts.server.v2_simulation_injection import list_injections
            return main.json_response(self,list_injections(main.ROOT))

        if parsed.path.startswith('/api/simulation/'):
            return main.json_response(self, {'error':'旧模拟数据库已停用；请通过 V2 原生导入管理模拟数据，不再创建物理数据表'},410)

        retired_get = {'/api/databases','/api/sqlite-tables','/api/sqlite-rows',
                       '/api/sqlite-time-range','/api/sqlite-meta',
                       '/api/hierarchy-options','/api/train-alignment-preview',
                       '/api/export-tables/list','/api/device-index',
                       '/api/telemetry-series','/api/fault-intervals',
                       '/api/preprocessing/status', '/api/backup/list'}
        if parsed.path in retired_get:
            return main.json_response(self,{'error':'旧物理表选择接口已停用；请使用 V2 逻辑参量接口'},410)


        if parsed.path == '/api/home/summary':
            return main.json_response(self, main.home_dashboard_snapshot())

        # AAS-specific resource monitoring (not the entire workstation).
        if parsed.path == "/api/monitor/software":
            try:
                from utils.monitor import monitor
                return main.json_response(self, monitor.get_software_status(
                    tasks=getattr(main, 'TRAINING_TASKS', {}),
                    task_lock=getattr(main, 'TASKS_LOCK', None),
                    task_resources=getattr(main, 'TASK_RESOURCE_TRACKER', None),
                ))
            except (OSError, RuntimeError, ValueError) as exc:
                return main.json_response(self, {"error": "软件资源统计不可用：" + str(exc)}, 503)

        # 监控API
        if parsed.path == "/api/monitor/system":
            try:
                from utils.monitor import monitor
                return main.json_response(self, monitor.get_system_status())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/monitor/api":
            try:
                from utils.monitor import monitor
                return main.json_response(self, monitor.get_api_metrics())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        # GUI 备份/恢复已退役：/api/backup/* 由上方 retired_get 统一返回 410，
        # 此处不再保留永远不可达的实现，避免误导维护者认为列表接口仍可用。

        if parsed.path == '/api/task-history':
            try:
                params = parse_qs(parsed.query)
                raw_limit = params.get('limit', ['50'])[0]
                try:
                    limit = max(1, min(100, int(raw_limit)))
                except (TypeError, ValueError, OverflowError):
                    limit = 50
                include_all = params.get('include_all', ['0'])[0] == '1'
                from scripts.server.task_runtime import build_task_history_summary
                summary = build_task_history_summary(main, 50)
                items = main.list_public_task_statuses(limit, include_all=include_all)
                return main.json_response(self, {
                    'items': items, 'total': summary['total'],
                    'limit': limit, 'include_all': include_all, 'summary': summary,
                })
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                return main.json_response(self, {'error': '任务历史暂时不可用', 'detail': type(exc).__name__}, 503)

        if parsed.path in ('/api/train-status', '/api/task-status'):
            try:
                params = parse_qs(parsed.query)
                task_id = params.get("task_id", [None])[0]

                if not task_id:
                    return main.json_response(self, {"error": "task_id is required"}, 400)

                task = main.get_public_task_status(task_id)
                if not task:
                    return main.json_response(self, {"error": "任务不存在"}, 404)

                return main.json_response(self, task)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/databases":
            return main.json_response(self, {"items": main.list_databases()})

        if parsed.path == "/api/simulation/status":
            return main.json_response(self, main.get_simulation_status())

        if parsed.path == "/api/simulation/fault-types":
            return main.json_response(self, {"items": main.get_simulation_fault_types()})

        if parsed.path == "/api/simulation/tables":
            return main.json_response(self, {"items": main.get_simulation_tables()})

        if parsed.path == "/api/simulation/fault-tables":
            return main.json_response(self, {"items": main.get_fault_tables()})

        if parsed.path == "/api/configs":
            return main.json_response(self, {"items": main.list_experiment_configs()})

        if parsed.path == "/api/experiments":
            query = parse_qs(parsed.query)
            exp_type = query.get("type", [None])[0]
            return main.json_response(self, {"items": main.list_experiment_summaries(exp_type)})

        if parsed.path == "/api/compare-experiments":
            query = parse_qs(parsed.query)
            left = query.get('left', [''])[0]
            right = query.get('right', [''])[0]
            try:
                from scripts.server.experiment_comparison import compare_archived_experiments
                return main.json_response(self, compare_archived_experiments(main.ROOT, left, right))
            except (ValueError, OSError) as exc:
                return main.json_response(self, {'error': str(exc)}, 400)

        if parsed.path == "/api/experiment-config":
            query = parse_qs(parsed.query)
            path = query.get("path", [""])[0]
            if not path:
                return main.json_response(self, {"error": "path is required"}, 400)
            try:
                config = main.load_experiment_config(path)
                return main.json_response(self, config)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        # Phase 5B: one public data model. Older table selection endpoints must
        # not be callable by a stale browser tab or a bookmarked client.
        # Database read endpoints are isolated from the HTTP entrypoint.
        data_route = GET_DATA_ROUTES.get(parsed.path)
        if data_route is not None:
            data_route(self, parsed, main)
            return


        if parsed.path == "/api/experiment-details":
            query = parse_qs(parsed.query)
            summary_path = query.get("path", [""])[0]
            if not summary_path:
                return main.json_response(self, {"error": "path is required"}, 400)
            try:
                payload = main.get_experiment_details(summary_path)
                return main.json_response(self, payload)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/algorithm-files":
            # 获取现有的算法文件列表
            try:
                detectors_dir = main.ROOT / "detectors"
                configs_dir = main.ROOT / "configs" / "detectors"

                # 获取代码文件
                code_files = []
                if detectors_dir.exists():
                    code_files = [f.name for f in detectors_dir.glob("*.py") if not f.name.startswith('_')]

                # 获取配置文件
                config_files = []
                if configs_dir.exists():
                    config_files = [f.name for f in configs_dir.glob("*.yaml")]

                return main.json_response(self, {
                    "code_files": sorted(code_files),
                    "config_files": sorted(config_files)
                })
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/dependencies/status":
            try:
                detector_status = main.check_all_detectors()
                return main.json_response(self, {"detectors": detector_status})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        # 依赖管理 - 获取所有检测器依赖状态
        if parsed.path == "/api/dependencies":
            try:
                status = main.check_all_detectors()
                return main.json_response(self, {
                    "status": "success",
                    "detectors": status
                })
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/algorithm-config-schema":
            try:
                query = parse_qs(parsed.query)
                algorithm_name = query.get("algorithm", [None])[0]
                if algorithm_name:
                    schema = main.get_detector_config_schema(algorithm_name)
                    return main.json_response(self, {"schema": schema})
                schemas = main.get_all_detector_schemas()
                return main.json_response(self, {"schemas": schemas})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/available-algorithms":
            # 获取已导入的算法列表（用于运行界面）
            # 算法清单的单一真相在 detectors.registry.DETECTOR_METADATA，
            # 内置/注册算法与 configs/detectors 下的自定义 YAML 算法合并去重。
            try:
                algorithms = main.get_available_algorithms()
                return main.json_response(self, {"algorithms": algorithms})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/model-input-sources":
            try:
                raw_id = parse_qs(parsed.query).get('model_id', [''])[0]
                model_id = int(raw_id)
                model = main.get_model(model_id)
                if not model:
                    return main.json_response(self, {'error': '模型不存在'}, 404)
                return main.json_response(self, model_input_sources(main, model))
            except (TypeError, ValueError):
                return main.json_response(self, {'error': '无效的模型ID'}, 400)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {'error': str(exc)}, 400)

        if parsed.path == '/api/v2/run-dataset/catalog':
            try:
                from scripts.server.telemetry_v2_routes import run_catalog
                return main.json_response(self,run_catalog(main,parse_qs(parsed.query)))
            except Exception as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path in ('/api/v2/user-csv-workspace/list', '/api/v2/user-csv-workspace/dataset'):
            try:
                from scripts.server import v2_user_csv_workspace as user_workspace
                lookup = getattr(main, 'get_public_task_status', None)
                if callable(lookup):
                    user_workspace.reconcile_task_refs(main.ROOT, lookup)
                    user_workspace.reconcile_imports(main.ROOT, lookup)
                query = parse_qs(parsed.query)
                if parsed.path.endswith('/dataset'):
                    dataset_id = (query.get('dataset_id') or [''])[0]
                    return main.json_response(self, user_workspace.get_dataset(main.ROOT, dataset_id))
                role = (query.get('role') or [None])[0]
                return main.json_response(self, {'datasets': user_workspace.list_datasets(main.ROOT, role)})
            except KeyError as exc:
                return main.json_response(self, {'error':str(exc)},404)
            except (ValueError, TypeError, OSError, sqlite3.DatabaseError) as exc:
                return main.json_response(self, {'error':str(exc)},400)

        if parsed.path == '/api/v2/models':
            try:
                from scripts.server.telemetry_v2_routes import v2_models
                return main.json_response(self,v2_models(main))
            except Exception as exc:
                return main.json_response(self,{'error':str(exc)},400)

        if parsed.path == "/api/models":
            try:
                query = parse_qs(parsed.query)
                status = query.get("status", [None])[0]
                algorithm = query.get("algorithm", [None])[0]
                limit = int(query.get("limit", ["50"])[0])
                if limit < 1 or limit > 500:
                    raise ValueError("limit must be between 1 and 500")
                models = main.get_models(status=status, algorithm=algorithm, limit=limit)
                return main.json_response(self, {"models": models, "total": len(models)})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path.startswith("/api/models/"):
            try:
                model_id = int(parsed.path.split("/api/models/")[1])
                model = main.get_model(model_id)
                if not model:
                    return main.json_response(self, {"error": "模型不存在"}, 404)
                return main.json_response(self, model)
            except (ValueError, IndexError):
                return main.json_response(self, {"error": "无效的模型ID"}, 400)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        # 仅允许读取 models/ 下的模型目录与 JSON 元数据，禁止任意文件系统访问。
        if parsed.path == "/api/model-dir-files":
            try:
                query = parse_qs(parsed.query)
                model_dir = query.get("dir", [None])[0]
                if not model_dir:
                    return main.json_response(self, {"error": "缺少 dir 参数"}, 400)

                model_path = main.resolve_managed_model_dir(model_dir)
                files = [f.name for f in model_path.iterdir() if f.is_file()]
                return main.json_response(
                    self,
                    {"files": sorted(files), "dir": model_path.relative_to(main.ROOT).as_posix()},
                )
            except FileNotFoundError as exc:
                return main.json_response(self, {"error": str(exc)}, 404)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/model-file-content":
            try:
                query = parse_qs(parsed.query)
                model_dir = query.get("dir", [None])[0]
                filename = query.get("file", [None])[0]

                if not model_dir or not filename:
                    return main.json_response(self, {"error": "缺少 dir 或 file 参数"}, 400)

                file_path = main.resolve_managed_model_file(model_dir, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                return main.json_response(
                    self,
                    {"content": content, "file": file_path.relative_to(main.ROOT).as_posix()},
                )
            except json.JSONDecodeError:
                return main.json_response(self, {"error": "文件不是有效的JSON"}, 400)
            except FileNotFoundError as exc:
                return main.json_response(self, {"error": str(exc)}, 404)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/plugins":
            try:
                return main.json_response(self, {"plugins": collect_plugins(main.ROOT)})
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        if parsed.path == "/api/plugins/native-info":
            try:
                import platform as _platform
                from core.native_plugins import NATIVE_ABI_V1, native_library_suffixes
                from core.native_build import native_build_capabilities
                build = native_build_capabilities()
                return main.json_response(self, {
                    "abi": NATIVE_ABI_V1,
                    "platform": _platform.system(),
                    "library_suffixes": list(native_library_suffixes()),
                    "supported_types": ["detectors", "metrics"],
                    "languages": ["c", "cpp"],
                    **build,
                })
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return main.json_response(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)

        # Keep the API namespace machine-readable even for unknown routes.
        # Falling through to SimpleHTTPRequestHandler would otherwise return
        # an HTML 404 page, breaking the JSON API error contract.
        if parsed.path.startswith("/api/"):
            return main.json_response(self, {"error": "not found"}, 404)
        return super().do_GET()

