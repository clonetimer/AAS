"""User-facing CSV simulation workspace contract: real rows, 1-based frames."""
from __future__ import annotations

import sqlite3
import hashlib
import base64
import csv
import io
import json
import shutil
import subprocess
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

import pytest

from scripts.server import v2_csv_workspace as csv_ws


def _import_csv(root, raw, filename):
    return csv_ws.import_csv(root, raw, filename, csv_encoding='utf-8',
                             csv_delimiter=',', format_confirmed=True)


def _source():
    return ('\ufefftime_s,label,adcs.rw.speed,eps.voltage,note\r\n'
            '0,0,10,28,"start, nominal"\r\n'
            '1,0,11,29,running\r\n'
            '2,1,12,30,"observed, fault"\r\n'
            '3,1,13,31,end\r\n').encode('utf-8')


def test_preview_csv_has_real_columns_and_frame_order_without_mutation(tmp_path):
    preview = csv_ws.inspect_csv(_source(), 'local.csv')
    assert preview['frame_count'] == 4
    assert preview['numeric_columns'] == ['adcs.rw.speed', 'eps.voltage']
    assert preview['label_column'] == 'label'
    assert preview['time_column'] == 'time_s'
    assert preview['sample'][0] == {'frame': 1, 'time_s': '0', 'label': '0',
                                   'adcs.rw.speed': '10', 'eps.voltage': '28',
                                   'note': 'start, nominal'}
    assert not (tmp_path / 'datasets').exists()


def test_import_is_isolated_and_persists_every_field(tmp_path):
    (tmp_path / 'datasets').mkdir()
    historical = tmp_path / 'datasets' / 'aas_telemetry_v2.sqlite'
    historical.write_bytes(b'HISTORICAL-DB-NOT-TOUCHED')
    saved = _import_csv(tmp_path, _source(), 'local.csv')
    assert saved['frame_count'] == 4 and saved['version'] == 0
    assert saved['id'] and saved['parent_id'] is None
    assert saved['root_sha256'] == saved['content_sha256']
    assert saved['has_truth_labels'] is True
    assert historical.read_bytes() == b'HISTORICAL-DB-NOT-TOUCHED'
    result = csv_ws.get_dataset(tmp_path, saved['id'])
    assert result['frame_count'] == 4
    assert [r['frame'] for r in result['frames']] == [1, 2, 3, 4]
    assert result['frames'][2]['row']['label'] == '1'
    assert result['frames'][2]['row']['note'] == 'observed, fault'
    assert result['events'] == []
    with sqlite3.connect(tmp_path / 'datasets' / 'aas_simulation_v2.sqlite') as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_csv_frames').fetchone()[0] == 4


@pytest.mark.parametrize('name,data', [
    ('sub/unsafe.csv', _source()),
    ('wrong.txt', _source()),
    ('empty.csv', b''),
    ('duplicate.csv', b'time_s,speed,speed\n0,1,2\n1,2,3\n'),
    ('bad-row.csv', b'time_s,speed\n0,1\n1,2,3\n'),
    ('binary.csv', b'time_s,speed\n\xff\n'),
    ('zero.csv', b'time_s,speed\n'),
    ('all-text.csv', b'time_s,note\n0,ok\n1,good\n'),
    ('nan.csv', b'time_s,x\n0,nan\n1,inf\n'),
])
def test_rejects_invalid_or_unsupported_csv(tmp_path, name, data):
    with pytest.raises(ValueError):
        _import_csv(tmp_path, data, name)
    assert not (tmp_path / 'datasets' / 'aas_simulation_v2.sqlite').exists()


def test_import_does_not_guess_normality_from_unlabeled_csv(tmp_path):
    raw = b'time_s,speed\n0,10\n1,11\n2,12\n'
    info = _import_csv(tmp_path, raw, 'telemetry.csv')
    assert info['has_truth_labels'] is False
    assert info['label_column'] is None
    assert info['source_truth'] == 'unlabeled'
    assert csv_ws.get_dataset(tmp_path, info['id'])['frames'][0]['row'] == {'time_s': '0', 'speed': '10'}


def _fault(source_id, column='adcs.rw.speed', fault_type='bias', start=2, end=4, magnitude=5):
    return {'dataset_id': source_id, 'column': column, 'fault_type': fault_type,
            'start_frame': start, 'end_frame': end, 'magnitude': magnitude}


def test_inclusive_frames_multiple_injections_and_immutable_history(tmp_path):
    original = _import_csv(tmp_path, _source(), 'local.csv')
    baseline = csv_ws.get_dataset(tmp_path, original['id'])
    config = _fault(original['id'], start=2, end=3, magnitude=5)
    dry = csv_ws.preview_fault(tmp_path, config)
    assert dry['fault_frames'] == 2
    assert [r['value'] for r in dry['points']] == [10, 16, 17, 13]
    first = csv_ws.save_fault(tmp_path, {**config, 'preview_sha256': dry['preview_sha256']})
    assert first['parent_id'] == original['id'] and first['version'] == 1
    assert csv_ws.get_dataset(tmp_path, original['id']) == baseline
    assert [r['row']['adcs.rw.speed'] for r in csv_ws.get_dataset(tmp_path, first['id'])['frames']] == ['10', '16', '17', '13']

    next_config = _fault(first['id'], fault_type='stuck', start=3, end=4, magnitude=2)
    second = csv_ws.save_fault(tmp_path, {**next_config,
        'preview_sha256': csv_ws.preview_fault(tmp_path, next_config)['preview_sha256']})
    two = csv_ws.get_dataset(tmp_path, second['id'])
    assert second['version'] == 2 and second['parent_id'] == first['id']
    assert [r['row']['adcs.rw.speed'] for r in two['frames']] == ['10', '16', '2', '2']
    assert [r['row']['label'] for r in two['frames']] == ['0', '0', '1', '1']
    assert [e['fault_type'] for e in two['events']] == ['bias', 'stuck']
    assert [(e['start_frame'], e['end_frame']) for e in two['events']] == [(2, 3), (3, 4)]
    assert [r['row']['adcs.rw.speed'] for r in csv_ws.get_dataset(tmp_path, first['id'])['frames']] == ['10', '16', '17', '13']


@pytest.mark.parametrize('change', [
    {'start_frame': 0}, {'end_frame': 5}, {'start_frame': 3, 'end_frame': 2},
    {'start_frame': 2.0}, {'end_frame': '4'}, {'column': 'label'},
    {'column': 'unknown'}, {'fault_type': 'unregistered_plugin'},
    {'magnitude': float('nan')}, {'magnitude': float('inf')},
    {'fault_type': 'bias', 'magnitude': 0},
])
def test_fault_preview_rejects_bad_1_based_intervals_and_columns(tmp_path, change):
    first = _import_csv(tmp_path, _source(), 'local.csv')
    with pytest.raises(ValueError):
        csv_ws.preview_fault(tmp_path, {**_fault(first['id']), **change})


def test_dry_run_does_not_write_and_save_requires_matching_preview_digest(tmp_path):
    first = _import_csv(tmp_path, _source(), 'local.csv')
    cfg = _fault(first['id'])
    dry = csv_ws.preview_fault(tmp_path, cfg)
    assert len(csv_ws.list_datasets(tmp_path)['items']) == 1
    with pytest.raises(ValueError):
        csv_ws.save_fault(tmp_path, {**cfg, 'preview_sha256': '0'*64})
    assert len(csv_ws.list_datasets(tmp_path)['items']) == 1
    result = csv_ws.save_fault(tmp_path, {**cfg, 'preview_sha256': dry['preview_sha256']})
    assert len(csv_ws.list_datasets(tmp_path)['items']) == 2
    assert result['id'] != first['id']


def test_zero_magnitude_stuck_is_meaningful(tmp_path):
    first = _import_csv(tmp_path, _source(), 'local.csv')
    result = csv_ws.preview_fault(tmp_path, _fault(first['id'],fault_type='stuck',start=1,end=2,magnitude=0))
    assert [r['value'] for r in result['points']] == [0, 0, 12, 13]


def test_complete_csv_export_keeps_all_rows_columns_and_event_chain(tmp_path):
    orig = _import_csv(tmp_path, _source(), 'local.csv')
    config = _fault(orig['id'], start=1, end=2)
    injected = csv_ws.save_fault(tmp_path, {**config,
        'preview_sha256':csv_ws.preview_fault(tmp_path, config)['preview_sha256']})
    export = csv_ws.export_csv(tmp_path, injected['id'])
    output = list(csv.DictReader(io.StringIO(export.decode('utf-8-sig'))))
    assert len(output) == 4
    assert list(output[0]) == ['aas_frame','time_s','label','adcs.rw.speed','eps.voltage','note',
                               'aas_injected_fault','aas_fault_events']
    assert [row['aas_frame'] for row in output] == ['1','2','3','4']
    assert [row['label'] for row in output] == ['0','0','1','1']
    assert output[0]['note'] == 'start, nominal'
    assert [row['adcs.rw.speed'] for row in output] == ['15','16','12','13']
    assert [row['aas_injected_fault'] for row in output] == ['1','1','0','0']
    assert output[0]['aas_fault_events'] and not output[2]['aas_fault_events']
    base = list(csv.DictReader(io.StringIO(csv_ws.export_csv(tmp_path,orig['id']).decode('utf-8-sig'))))
    assert [row['adcs.rw.speed'] for row in base] == ['10','11','12','13']


def test_excel_safe_export_protects_formulas_with_explicit_raw_mode(tmp_path):
    raw = b'time_s,x,note\n0,1,"=HYPERLINK(""a"",""b"")"\n1,2,@danger\n'
    info = _import_csv(tmp_path, raw, 'foo.csv')
    safe = list(csv.DictReader(io.StringIO(csv_ws.export_csv(tmp_path,info['id']).decode('utf-8-sig'))))
    assert safe[0]['note'].startswith("'=HYPERLINK")
    assert safe[1]['note'] == "'@danger"
    original = list(csv.DictReader(io.StringIO(csv_ws.export_csv(tmp_path,info['id'],spreadsheet_safe=False).decode('utf-8-sig'))))
    assert original[0]['note'].startswith('=HYPERLINK')
    assert original[1]['note'] == '@danger'


def test_authenticated_http_upload_preview_save_list_export(tmp_path):
    from scripts import visualization_server as app
    from scripts.server.home_auth import HomeAuth

    auth=HomeAuth(tmp_path)
    with patch.object(app,'ROOT',tmp_path), patch.object(app,'HOME_AUTH',auth):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.DashboardHandler)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        def post(path,body,headers=None):
            req=Request(origin+path,data=body,headers=headers or {'Content-Type':'application/json'})
            with urlopen(req,timeout=8) as resp:return json.load(resp),resp.headers.get('Set-Cookie','')
        def get(path,cookie):
            with urlopen(Request(origin+path,headers={'Cookie':cookie}),timeout=8) as resp:
                return resp.read(),resp.headers
        try:
            post('/api/auth/setup',json.dumps({'username':'admin','password':'a-strong-password-for-testing'}).encode())
            _,cookie=post('/api/auth/login',json.dumps({'username':'admin','password':'a-strong-password-for-testing'}).encode())
            cookie=cookie.split(';',1)[0]
            headers={'Cookie':cookie,'Content-Type':'application/octet-stream','X-AAS-CSV-Filename':'local.csv',
                    'X-AAS-CSV-Format-B64':base64.b64encode(json.dumps({
                        'csv_encoding':'utf-8','csv_delimiter':',','format_confirmed':True
                    }).encode()).decode()}
            inspected,_=post('/api/v2/csv-workspace/inspect',_source(),headers)
            assert inspected['frame_count']==4
            from urllib.error import HTTPError
            unconfirmed={k:v for k,v in headers.items() if k!='X-AAS-CSV-Format-B64'}
            with pytest.raises(HTTPError) as rejected:
                post('/api/v2/csv-workspace/import',_source(),unconfirmed)
            assert rejected.value.code==400
            imported,_=post('/api/v2/csv-workspace/import',_source(),headers)
            ident=imported['id'];assert ident
            dry,_=post('/api/v2/csv-workspace/fault-preview',json.dumps(_fault(ident,start=2,end=4)).encode(),
                       {'Cookie':cookie,'Content-Type':'application/json'})
            saved,_=post('/api/v2/csv-workspace/fault-save',json.dumps({**_fault(ident,start=2,end=4),
                         'preview_sha256':dry['preview_sha256']}).encode(),
                         {'Cookie':cookie,'Content-Type':'application/json'})
            listing,_=get('/api/v2/csv-workspace/list',cookie)
            items=json.loads(listing)['items']
            assert len(items)==2 and any(x['id']==saved['id'] for x in items)
            sampled,_=get(f'/api/v2/csv-workspace/dataset-preview?id={saved["id"]}&column=adcs.rw.speed',cookie)
            assert json.loads(sampled)['frame_count']==4
            binary,meta=get(f'/api/v2/csv-workspace/export?id={saved["id"]}',cookie)
            assert 'attachment' in meta['Content-Disposition'].lower()
            assert len(list(csv.DictReader(io.StringIO(binary.decode('utf-8-sig')))))==4
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


def test_csv_browser_ui_contract():
    if not shutil.which('node'):
        pytest.skip('本环境没有 Node；跳过浏览器脚本契约测试')
    result=subprocess.run(['node','tests/csv_workspace_ui.cjs'],text=True,capture_output=True,check=False)
    assert result.returncode==0, result.stdout+'\n'+result.stderr


def test_runner_csv_upload_contract():
    if not shutil.which('node'):
        pytest.skip('Node 不可用')
    result=subprocess.run(['node','tests/runner_csv_import_ui.cjs'],text=True,capture_output=True,check=False)
    assert result.returncode==0, result.stdout+'\n'+result.stderr


def test_simulation_inspection_detects_gb18030_semicolon_and_requires_confirmation(tmp_path):
    raw = ('time_s;adcs.rw.speed;note\r\n'
           '0;1.5;"中文;说明"\r\n'
           '1;2.5;"第二行,含逗号"\r\n').encode('gb18030')
    inspected = csv_ws.inspect_csv(raw, '现场.csv', csv_encoding='auto', csv_delimiter='auto')
    assert (inspected['csv_encoding'], inspected['csv_delimiter']) == ('gb18030', ';')
    assert inspected['format_review_required'] is True
    assert inspected['sample'][0]['note'] == '中文;说明'
    with pytest.raises(ValueError, match='确认'):
        csv_ws.import_csv(tmp_path, raw, '现场.csv', csv_encoding='gb18030',
                          csv_delimiter=';', format_confirmed=False)
    saved = csv_ws.import_csv(tmp_path, raw, '现场.csv', csv_encoding='gb18030',
                              csv_delimiter=';', format_confirmed=True)
    assert saved['csv_format'] == {'encoding': 'gb18030', 'delimiter': ';', 'compression': 'none'}
    assert saved['root_sha256'] == hashlib.sha256(raw).hexdigest()
    assert csv_ws.get_dataset(tmp_path, saved['id'])['frames'][0]['row']['note'] == '中文;说明'


def test_simulation_inspection_supports_utf8_bom_tab_and_csv_gzip(tmp_path):
    import gzip
    tabbed = '\ufefftime_s\tvoltage\tlabel\r\n0\t28.1\t0\r\n1\t28.2\t1\r\n'.encode('utf-8')
    inspected = csv_ws.inspect_csv(tabbed, 'tab.csv')
    assert (inspected['csv_encoding'], inspected['csv_delimiter']) == ('utf-8', '\t')
    saved = csv_ws.import_csv(tmp_path, tabbed, 'tab.csv', csv_encoding='utf-8',
                              csv_delimiter='\t', format_confirmed=True)
    assert saved['numeric_columns'] == ['voltage']

    compressed = gzip.compress(('time_s;speed;note\n0;1;中文\n1;2;正常\n').encode('gb18030'))
    inspected_gzip = csv_ws.inspect_csv(compressed, '现场.csv.gz')
    assert inspected_gzip['csv_encoding'] == 'gb18030'
    assert inspected_gzip['csv_delimiter'] == ';'
    gzip_saved = csv_ws.import_csv(tmp_path, compressed, '现场.csv.gz', csv_encoding='gb18030',
                                   csv_delimiter=';', format_confirmed=True)
    assert gzip_saved['csv_format']['compression'] == 'gzip'
    assert gzip_saved['root_sha256'] == hashlib.sha256(compressed).hexdigest()
    config = {**_fault(gzip_saved['id'], start=1, end=2), 'column':'speed'}
    dry = csv_ws.preview_fault(tmp_path, config)
    derived = csv_ws.save_fault(tmp_path, {**config, 'preview_sha256':dry['preview_sha256']})
    assert csv_ws.get_dataset(tmp_path, derived['id'])['csv_format'] == gzip_saved['csv_format']


def test_simulation_import_rejects_auto_or_unconfirmed_format_before_database_write(tmp_path):
    raw = 'time_s;speed\n0;1\n1;2\n'.encode('utf-8')
    with pytest.raises(ValueError, match='确认'):
        csv_ws.import_csv(tmp_path, raw, 'local.csv', csv_encoding='auto',
                          csv_delimiter=';', format_confirmed=True)
    with pytest.raises(ValueError, match='确认'):
        csv_ws.import_csv(tmp_path, raw, 'local.csv', csv_encoding='utf-8',
                          csv_delimiter=';', format_confirmed=False)
    assert not (tmp_path / 'datasets' / 'aas_simulation_v2.sqlite').exists()


def test_existing_simulation_workspace_schema_migrates_format_metadata(tmp_path):
    import sqlite3
    from scripts.server.v2_simulation_injection import initialize
    path = tmp_path / 'datasets' / 'aas_simulation_v2.sqlite'
    initialize(path)
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE v2_csv_datasets (
            id TEXT PRIMARY KEY, parent_id TEXT, filename TEXT NOT NULL, version INTEGER NOT NULL,
            columns_json TEXT NOT NULL, numeric_columns_json TEXT NOT NULL,
            time_column TEXT, label_column TEXT, has_truth_labels INTEGER NOT NULL,
            source_truth TEXT NOT NULL, root_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            frame_count INTEGER NOT NULL, events_json TEXT NOT NULL, created_utc TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE v2_csv_frames (
            dataset_id TEXT NOT NULL, frame INTEGER NOT NULL, row_json TEXT NOT NULL,
            PRIMARY KEY(dataset_id,frame))''')
    raw = b'time_s,speed\n0,1\n1,2\n'
    saved = csv_ws.import_csv(tmp_path, raw, 'legacy.csv', csv_encoding='utf-8',
                              csv_delimiter=',', format_confirmed=True)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(v2_csv_datasets)')}
    assert 'csv_format_json' in columns
    assert csv_ws.get_dataset(tmp_path, saved['id'])['csv_format']['encoding'] == 'utf-8'


def test_sim_csv_quality_inspect_saved_and_inherited_across_fault_versions(tmp_path):
    raw=b'time_s,speed,temp,label,note\n0,10,20,0,ok\n1,11,,0,missing\n2,12,NaN,1,nan\n3,13,24,X,invalid_label\n'
    inspected=csv_ws.inspect_csv(raw,'quality.csv')
    assert inspected['quality']['missing_by_column']=={'temp':1}
    assert inspected['quality']['nonfinite_by_column']=={'temp':1,'note':1}
    assert inspected['quality']['label_counts']=={'0':2,'1':1,'unknown':1}
    assert inspected['numeric_columns']==['speed']
    first=_import_csv(tmp_path,raw,'quality.csv')
    assert first['quality']==inspected['quality']
    base=csv_ws.get_dataset(tmp_path,first['id'])
    assert base['quality']==inspected['quality']
    assert csv_ws.list_datasets(tmp_path)['items'][0]['quality']==inspected['quality']
    fault=_fault(first['id'],column='speed',start=2,end=3)
    child=csv_ws.save_fault(tmp_path,{**fault,
        'preview_sha256':csv_ws.preview_fault(tmp_path,fault)['preview_sha256']})
    assert csv_ws.get_dataset(tmp_path,child['id'])['quality']==inspected['quality']


def test_sim_csv_quality_migrates_old_database_table_without_changing_records(tmp_path):
    (tmp_path/'datasets').mkdir()
    db=tmp_path/'datasets'/'aas_simulation_v2.sqlite'
    csv_ws.initialize(db)
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE v2_csv_datasets(
            id TEXT PRIMARY KEY, parent_id TEXT, filename TEXT NOT NULL, version INTEGER NOT NULL,
            columns_json TEXT NOT NULL, numeric_columns_json TEXT NOT NULL,
            time_column TEXT, label_column TEXT, has_truth_labels INTEGER NOT NULL,
            source_truth TEXT NOT NULL, root_sha256 TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, frame_count INTEGER NOT NULL,
            events_json TEXT NOT NULL, created_utc TEXT NOT NULL,
            csv_format_json TEXT NOT NULL DEFAULT '{}')''')
        conn.execute('''CREATE TABLE v2_csv_frames(dataset_id TEXT NOT NULL,frame INTEGER NOT NULL,
            row_json TEXT NOT NULL,PRIMARY KEY(dataset_id,frame))''')
    added=_import_csv(tmp_path,b'time_s,speed\n0,1\n1,2\n','old.csv')
    with sqlite3.connect(db) as conn:
        assert 'quality_json' in [x[1] for x in conn.execute('PRAGMA table_info(v2_csv_datasets)')]
    assert csv_ws.get_dataset(tmp_path,added['id'])['quality']['row_count']==2
