"""Database HTTP API routes for the dashboard.

The public URL, parameter, status and response contracts are retained from
DashboardHandler. The main module is passed explicitly to preserve legacy
compatibility adapters and test injection. The route maps contain exact paths
only; unmatched API paths remain 404 in DashboardHandler.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from urllib.parse import parse_qs

def _v2_file(main):
    from pathlib import Path
    from scripts.server.v2_database_config import active_path
    return active_path(main.ROOT)

def _get_v2_connection(handler, parsed, main):
    from scripts.server.v2_database_config import active_name
    from scripts.server.telemetry_v2_access import open_database
    path = _v2_file(main)
    result = {'database':active_name(),'connected':False,'schema_valid':False}
    if path.is_file():
        try:
            with open_database(path) as conn:
                result['schema_valid']=True
                result['connected']=True
                result['has_run_annotations']=bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_annotations'").fetchone())
        except (sqlite3.Error,ValueError,FileNotFoundError):
            pass
    return main.json_response(handler,result)


def _get_v2_databases(handler, parsed, main):
    from scripts.server.v2_database_config import list_databases, active_name
    return main.json_response(handler, {'databases':list_databases(main.ROOT), 'default':active_name()})


def _get_v2_sources(handler, parsed, main):
    from scripts.server.v2_source_delete import list_sources
    q = parse_qs(parsed.query)
    try:
        path = _browser_db(main, q.get('database', [None])[0])
        return main.json_response(handler, list_sources(path, int(q.get('limit', ['500'])[0])))
    except (ValueError, TypeError, FileNotFoundError, sqlite3.Error) as exc:
        return main.json_response(handler, {'error': str(exc)}, 400)


def _post_v2_source_delete(handler, parsed, main, raw):
    from scripts.server.v2_source_delete import preview, delete
    try:
        if len(raw) > 4096:
            raise ValueError('删除请求过大')
        request = json.loads(raw.decode('utf-8'))
        if not isinstance(request, dict):
            raise ValueError('删除请求必须是对象')
        commit = parsed.path.endswith('/commit')
        required = {'database', 'source_id', 'preview_token', 'confirm_name', 'confirm'} if commit else {'database', 'source_id'}
        if set(request) != required:
            raise ValueError('删除请求字段不符合预期')
        path = _browser_db(main, request['database'])
        if not commit:
            return main.json_response(handler, preview(main.ROOT, path, request['source_id']))
        if request['confirm'] is not True:
            raise ValueError('删除数据源必须明确确认')
        tasks = main.list_public_task_statuses(100, include_all=True)
        active = any(item.get('status') in ('running', 'cancelling') for item in tasks)
        actor = handler.authenticated_user(main) if handler is not None and hasattr(handler, 'authenticated_user') else 'internal'
        result = delete(main.ROOT, path, request['source_id'],
                        preview_token=request['preview_token'],
                        confirm_name=request['confirm_name'], active_tasks=active,
                        actor=actor)
        return main.json_response(handler, result)
    except KeyError as exc:
        return main.json_response(handler, {'error': str(exc)}, 404)
    except RuntimeError as exc:
        return main.json_response(handler, {'error': str(exc)}, 409)
    except (ValueError, TypeError, UnicodeDecodeError, FileNotFoundError,
            json.JSONDecodeError, sqlite3.Error) as exc:
        return main.json_response(handler, {'error': str(exc)}, 400)


def _browser_db(main,name):
    from scripts.server.v2_database_config import resolve_database
    return resolve_database(main.ROOT,name)


def _get_v2_filters(handler, parsed, main):
    from scripts.server.telemetry_v2_access import filter_options
    q=parse_qs(parsed.query)
    try:
        filters={key:q[key][0] for key in ('namespace','satellite_instance','model','subsystem','device','variable','keyword') if key in q}
        return main.json_response(handler, filter_options(
            _browser_db(main,q.get('database',[None])[0]), role=q.get('role',['unspecified'])[0], filters=filters))
    except (ValueError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler, {'error':str(exc)},400)

def _get_v2_series(handler, parsed, main):
    from scripts.server.telemetry_v2_access import list_channels
    q=parse_qs(parsed.query)
    try:
        filters={key:q[key][0] for key in ('namespace','satellite_instance','model','subsystem','device','variable','keyword') if key in q}
        limit=int(q.get('limit',['50'])[0])
        offset=int(q.get('offset',['0'])[0])
        if limit > 100:
            raise ValueError('limit 必须为 1～100')
        return main.json_response(handler,list_channels(_browser_db(main,q.get('database',[None])[0]),role=q.get('role',['unspecified'])[0],filters=filters,
            start=q.get('start',[None])[0],end=q.get('end',[None])[0],limit=limit,offset=offset))
    except (ValueError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler, {'error':str(exc)},400)

def _get_v2_fault_finder(handler, parsed, main):
    from scripts.server.telemetry_v2_fault_finder import fault_index
    q = parse_qs(parsed.query)
    try:
        filters={key:q[key][0] for key in ('namespace','model','subsystem','device','keyword') if key in q}
        output=fault_index(_browser_db(main,q.get('database',[None])[0]),
            role=q.get('role',['test'])[0], filters=filters,
            limit=int(q.get('limit',['500'])[0]),fault_type=q.get('fault_type',[None])[0])
        return main.json_response(handler,output)
    except (ValueError,TypeError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)


def _get_v2_fault_associations(handler, parsed, main):
    from scripts.server.telemetry_v2_fault_associations import fault_associations
    q = parse_qs(parsed.query)
    try:
        data = fault_associations(_browser_db(main, q.get('database', [None])[0]),
            role=q.get('role', ['test'])[0], model=q.get('model', [''])[0],
            namespace=q.get('namespace', [None])[0],
            fault_type=q.get('fault_type', [None])[0],
            limit=int(q.get('limit', ['500'])[0]))
        return main.json_response(handler, data)
    except (ValueError, TypeError, FileNotFoundError, sqlite3.Error) as exc:
        return main.json_response(handler, {'error': str(exc)}, 400)


def _get_v2_channel_coverage(handler, parsed, main):
    from scripts.server.telemetry_v2_access import channel_time_coverage
    q=parse_qs(parsed.query)
    try:
        raw=q.get('channel_ids',[''])[0]
        if not raw or not all(x.isdecimal() for x in raw.split(',')):
            raise ValueError('请选择有效的参量 ID')
        ids=[int(x) for x in raw.split(',')]
        payload=channel_time_coverage(_browser_db(main,q.get('database',[None])[0]),
            role=q.get('role',['unspecified'])[0],channel_ids=ids,
            mode=q.get('mode',['intersection'])[0])
        return main.json_response(handler,payload)
    except (ValueError,TypeError,OverflowError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)

def _post_v2_query(handler, parsed, main, raw):
    from scripts.server.telemetry_v2_access import query_charts
    try:
        body=json.loads(raw)
        if not isinstance(body,dict):raise ValueError('请求体必须是对象')
        mode=body.get('mode','overview')
        default_limit=100 if mode=='page' else 2000
        payload=query_charts(_browser_db(main,body.get('database')),role=body.get('role'),channel_ids=body.get('channel_ids'),
             start=body.get('start'),end=body.get('end'),limit=body.get('limit',default_limit),
             mode=mode,cursor=body.get('cursor'))
        return main.json_response(handler,payload)
    except (ValueError,TypeError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)

def _post_v2_selection_export(handler, parsed, main, raw):
    """Download complete selected V2 channel data, not capped chart preview rows."""
    from scripts.server.telemetry_v2_selection_export import build_selection_export
    try:
        if len(raw) > 16_000:
            raise ValueError('导出条件过大')
        payload=json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('导出条件必须是 JSON 对象')
        stream, mime, filename = build_selection_export(
            _browser_db(main,payload.get('database')),
            role=payload.get('role'), channel_ids=payload.get('channel_ids'),
            start=payload.get('start'), end=payload.get('end'))
        try:
            stream.seek(0,2)
            length=stream.tell()
            stream.seek(0)
            handler.send_response(200)
            handler.send_header('Content-Type',mime)
            handler.send_header('Content-Length',str(length))
            handler.send_header('Content-Disposition',"attachment; filename=aas_selected_telemetry.csv; filename*=UTF-8''AAS_%E6%89%80%E9%80%89%E9%81%A5%E6%B5%8B%E5%8F%82%E9%87%8F.csv")
            handler.send_header('Cache-Control','no-store')
            handler.end_headers()
            shutil.copyfileobj(stream,handler.wfile,length=128*1024)
        finally:
            stream.close()
    except (ValueError, TypeError, OverflowError, UnicodeDecodeError, FileNotFoundError, OSError, sqlite3.Error) as exc:
        return main.json_response(handler, {'error':str(exc)},400)


def _get_v2_imports(handler, parsed, main):
    from scripts.server.telemetry_v2_import import list_imports
    q=parse_qs(parsed.query)
    try:
        limit=int(q.get('limit',['30'])[0])
        return main.json_response(handler,list_imports(_v2_file(main),limit=limit))
    except (ValueError,TypeError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)

def _post_v2_import(handler, parsed, main, raw):
    from scripts.server.telemetry_v2_import import import_csv, MAX_CSV_BYTES
    try:
        if len(raw) > MAX_CSV_BYTES * 2:
            raise ValueError('上传请求过大，请分批导入 CSV')
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('上传内容必须是对象')
        commit = payload.get('commit') is True
        result = import_csv(_v2_file(main), payload, commit=commit)
        return main.json_response(handler, result)
    except (ValueError, TypeError, FileNotFoundError, UnicodeDecodeError, sqlite3.Error) as exc:
        return main.json_response(handler, {'error':str(exc)}, 400)


def _post_v2_import_wide(handler, parsed, main, raw):
    from scripts.server.telemetry_v2_wide_import import import_wide_csv
    try:
        if len(raw)>8_000_000:raise ValueError('上传请求超出 8MB 上限')
        payload=json.loads(raw.decode('utf-8'))
        return main.json_response(handler,import_wide_csv(_v2_file(main),payload,commit=payload.get('commit') is True))
    except (ValueError,TypeError,FileNotFoundError,UnicodeDecodeError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)


def _get_v2_governance(handler, parsed, main):
    from scripts.server import telemetry_v2_governance as gov
    q=parse_qs(parsed.query)
    try:
        if parsed.path=='/api/v2/telemetry/datasets':
            result=gov.dataset_summary(_v2_file(main))
        elif parsed.path=='/api/v2/telemetry/segments':
            cid=int(q['channel_id'][0]) if 'channel_id' in q else None
            result=gov.list_segments(_v2_file(main),channel_id=cid,role=q.get('role',[None])[0],
                                     limit=int(q.get('limit',['200'])[0]))
        elif parsed.path=='/api/v2/telemetry/events':
            if 'channel_id' not in q or 'role' not in q:
                raise ValueError('事件查询需要 channel_id 和 role')
            result=gov.list_events(_v2_file(main),channel_id=int(q['channel_id'][0]),
                                   role=q['role'][0],include_retracted=q.get('include_retracted',['false'])[0]=='true')
        else:
            result=gov.list_audit(_v2_file(main),limit=int(q.get('limit',['100'])[0]))
        return main.json_response(handler,result)
    except (ValueError,TypeError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)


def _post_v2_governance(handler, parsed, main, raw):
    from scripts.server import telemetry_v2_governance as gov
    try:
        if len(raw)>32_000:raise ValueError('治理请求体过大')
        payload=json.loads(raw.decode('utf-8'))
        actor=handler.authenticated_user(main) if handler is not None and hasattr(handler,'authenticated_user') else 'internal'
        actor=str(actor or 'unknown')[:120]
        if parsed.path=='/api/v2/telemetry/segment/status':
            result=gov.set_segment_status(_v2_file(main),payload,actor=actor)
        elif parsed.path=='/api/v2/telemetry/events/create':
            result=gov.create_event(_v2_file(main),payload,actor=actor)
        else:
            result=gov.retract_event(_v2_file(main),payload,actor=actor)
        return main.json_response(handler,result)
    except (ValueError,TypeError,FileNotFoundError,UnicodeDecodeError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)

def _get_v2_preprocess_quality(handler, parsed, main):
    from scripts.server.telemetry_v2_preprocess import list_quality_jobs
    try:
        query=parse_qs(parsed.query)
        limit=int(query.get('limit',['30'])[0])
        return main.json_response(handler,list_quality_jobs(_v2_file(main),limit=limit))
    except (ValueError,TypeError,FileNotFoundError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)


def _post_v2_preprocess(handler, parsed, main, raw):
    """Single V2 preprocessor interface; no legacy table mode."""
    import json
    from scripts.server.telemetry_v2_preprocess import preprocess
    try:
        if len(raw)>16_000: raise ValueError('预处理请求超过大小限制')
        request=json.loads(raw.decode('utf-8'))
        actor=handler.authenticated_user(main) if handler is not None and hasattr(handler,'authenticated_user') else 'internal'
        result=preprocess(_v2_file(main),request,
           commit=parsed.path.endswith('/commit'),actor=str(actor or 'unknown'))
        return main.json_response(handler,result)
    except (ValueError,TypeError,FileNotFoundError,UnicodeDecodeError,sqlite3.Error) as exc:
        return main.json_response(handler,{'error':str(exc)},400)


GET_DATA_ROUTES = {
    "/api/v2/database/status": _get_v2_connection,
    "/api/v2/telemetry/datasets": _get_v2_governance,
    "/api/v2/telemetry/segments": _get_v2_governance,
    "/api/v2/telemetry/events": _get_v2_governance,
    "/api/v2/telemetry/governance/audit": _get_v2_governance,
    "/api/v2/telemetry/imports": _get_v2_imports,
    "/api/v2/telemetry/filter-options": _get_v2_filters,
    "/api/v2/telemetry/databases": _get_v2_databases,
    "/api/v2/telemetry/sources": _get_v2_sources,
    "/api/v2/telemetry/series/search": _get_v2_series,
    "/api/v2/telemetry/fault-finder": _get_v2_fault_finder,
    "/api/v2/telemetry/fault-associations": _get_v2_fault_associations,
    "/api/v2/telemetry/preprocess/jobs": _get_v2_preprocess_quality,
    "/api/v2/telemetry/series/coverage": _get_v2_channel_coverage,
}

POST_DATA_ROUTES = {
    "/api/v2/telemetry/source/delete/preview": _post_v2_source_delete,
    "/api/v2/telemetry/source/delete/commit": _post_v2_source_delete,
    "/api/v2/telemetry/preprocess/preview": _post_v2_preprocess,
    "/api/v2/telemetry/preprocess/commit": _post_v2_preprocess,
    "/api/v2/telemetry/segment/status": _post_v2_governance,
    "/api/v2/telemetry/events/create": _post_v2_governance,
    "/api/v2/telemetry/events/retract": _post_v2_governance,
    "/api/v2/telemetry/import-csv": _post_v2_import,
    "/api/v2/telemetry/import-table": _post_v2_import_wide,
    "/api/v2/telemetry/query": _post_v2_query,
    "/api/v2/telemetry/export-selection": _post_v2_selection_export,
}

