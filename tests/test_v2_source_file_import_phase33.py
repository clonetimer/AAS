"""Phase 33: gzip/SQLite read-only conversion, preview and atomic commit."""
import csv
import gzip
import hashlib
import io
from pathlib import Path
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_access import list_channels, query_charts
from scripts.server.telemetry_v2_source_import import source_operation, MAX_UPLOAD_BYTES


def dest(tmp_path):
    path = tmp_path / 'target.sqlite'
    with sqlite3.connect(path) as conn:
        create_v2(conn)
    return path


def meta(name, **updates):
    props = dict(filename=name, operation='preview', csv_encoding='utf-8',
                 csv_delimiter=',', format_confirmed=True, config={
        'namespace':'external_run_A','model':'SAT-X','subsystem':'ADCS',
        'device':'RW1','variable':'unused','unit':'rad/s','role':'test',
        'time_domain':'numeric_unspecified','time_reference':'run_A', 'time_unit':'s',
        'column_metadata':{'adcs.rw.speed':{
            'subsystem':'ADCS','device':'RW1','variable':'speed','unit':'rad/s'}},
        'unlabeled_test_confirmed':True})
    props.update(updates)
    return props


def gzip_data(text):
    return gzip.compress(text.encode('utf-8'))


def counts(path):
    with sqlite3.connect(path) as db:
        return [db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
                ('v2_sources','v2_channels','v2_segments','v2_samples')]


def test_gzip_header_preview_commit_and_original_provenance(tmp_path):
    path=dest(tmp_path)
    raw=gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,,1\n2,5,1\n')
    info=source_operation(path,raw,{'operation':'inspect','filename':'run.csv.gz'})
    assert info['format']=='csv.gz' and info['columns']==['time_s','adcs.rw.speed','label']
    cfg=meta('run.csv.gz')
    before=counts(path)
    result=source_operation(path,raw,cfg)
    assert result['missing_by_column']=={'adcs.rw.speed':1}
    assert counts(path)==before
    done=source_operation(path,raw,{**cfg,'operation':'commit'})
    assert done['status']=='imported' and done['row_count']==3
    assert counts(path)==[1,1,1,3]
    with sqlite3.connect(path) as db:
        source=db.execute('SELECT source_name,source_sha256,source_bytes FROM v2_sources').fetchone()
        assert source==('run.csv.gz',hashlib.sha256(raw).hexdigest(),len(raw))
    channel=done['channels'][0]['channel_id']
    assert query_charts(path,role='test',channel_ids=[channel],limit=10)['series'][0]['matched_count']==3
    with pytest.raises(ValueError,match='重叠'):
        source_operation(path,raw,{**cfg,'operation':'commit'})
    assert counts(path)==[1,1,1,3]


def test_gzip_duplicate_local_filename_and_unlabeled_guard(tmp_path):
    path=dest(tmp_path)
    raw=gzip_data('time_s,adcs.rw.speed\n0,1\n1,\n')
    assert source_operation(path,raw,{'operation':'inspect','filename':'local.csv(1).gz'})['has_labels'] is False
    config=meta('local.csv(1).gz')
    config['config']['role']='train'
    with pytest.raises(ValueError,match='确认'):
        source_operation(path,raw,config)
    config['config']['all_normal_confirmed']=True
    done=source_operation(path,raw,{**config,'operation':'commit'})
    assert done['label_status']=='confirmed_normal' and counts(path)==[1,1,1,2]


def test_gzip_reject_corrupt_bomb_and_trailing(tmp_path):
    path=dest(tmp_path)
    with pytest.raises(ValueError,match='GZIP'):
        source_operation(path,b'junk',{'operation':'inspect','filename':'x.csv.gz'})
    with pytest.raises(ValueError,match='尾部'):
        source_operation(path,gzip_data('x\n1\n')+b'abc',{'operation':'inspect','filename':'x.csv.gz'})
    bomb=gzip.compress(b'a'*12_000_100)
    with pytest.raises(ValueError,match='12MB'):
        source_operation(path,bomb,{'operation':'inspect','filename':'x.csv.gz'})
    assert counts(path)==[0,0,0,0]


def sqlite_file(tmp_path):
    source=tmp_path/'external.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE telemetry (time_s REAL, "adcs.rw.speed" REAL, label INTEGER)')
        db.executemany('INSERT INTO telemetry VALUES (?,?,?)',[(0,1,0),(1,None,1),(2,4,1)])
        db.execute('CREATE TABLE unrelated (item TEXT)')
        db.execute('INSERT INTO unrelated VALUES ("not telemetry")')
        db.execute('CREATE VIEW view_telemetry AS SELECT * FROM telemetry')
    return source.read_bytes()


def test_sqlite_tables_read_only_and_target_same_pipeline(tmp_path):
    path=dest(tmp_path)
    original=sqlite_file(tmp_path)
    opts=source_operation(path,original,{'filename':'external.db','operation':'inspect'})
    assert len(opts['tables'])==1 and opts['tables'][0]['name']=='telemetry'
    cfg=meta('external.db',table='telemetry')
    result=source_operation(path,original,cfg)
    assert result['row_count']==3 and result['missing_by_column']['adcs.rw.speed']==1
    assert counts(path)==[0,0,0,0]
    outcome=source_operation(path,original,{**cfg,'operation':'commit'})
    assert outcome['source_format']=='sqlite' and outcome['source_table']=='telemetry'
    assert counts(path)==[1,1,1,3]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT source_name FROM v2_sources').fetchone()[0]=='external.db # telemetry'
    assert source_operation(path,original,{'filename':'external.db','operation':'inspect'})==opts
    with pytest.raises(ValueError,match='符合遥测结构'):
        source_operation(path,original,{**cfg,'table':'sqlite_master','operation':'preview'})


def test_sqlite_reject_broken_schema_and_data_quality(tmp_path):
    path=dest(tmp_path)
    with pytest.raises(ValueError,match='文件头'):
        source_operation(path,b'\0'*200,{'filename':'evil.db','operation':'inspect'})
    source=tmp_path/'blob.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE logs (time_s, "adcs.rw.speed")')
        db.execute('INSERT INTO logs VALUES(0,?)',(b'HELLO',))
    with pytest.raises(ValueError,match='BLOB'):
        source_operation(path,source.read_bytes(),meta('blob.sqlite',table='logs'))
    assert counts(path)==[0,0,0,0]


def test_sqlite_invalid_labels_are_rejected_without_partial_write(tmp_path):
    path=dest(tmp_path)
    src=tmp_path/'bad_labels.db'
    with sqlite3.connect(src) as conn:
        conn.execute('CREATE TABLE t (time_s, "adcs.rw.speed", label)')
        conn.executemany('INSERT INTO t VALUES (?,?,?)',[(0,1,0),(1,1,4)])
    with pytest.raises(ValueError,match='标签'):
        source_operation(path,src.read_bytes(),{**meta('bad_labels.db',table='t'),'operation':'commit'})
    assert counts(path)==[0,0,0,0]


def test_http_binary_route_uses_configured_v2_path(tmp_path, monkeypatch):
    import base64
    import json
    from types import SimpleNamespace
    from urllib.parse import urlparse
    from scripts.server import handlers

    dataset = tmp_path/'datasets'
    dataset.mkdir()
    target=dataset/'aas_telemetry_v2.sqlite'
    with sqlite3.connect(target) as db:
        create_v2(db)
    raw=gzip_data('time_s,adcs.rw.speed,label\n0,1,0\n')
    m={'filename':'a.csv.gz','operation':'inspect'}
    results=[]
    main=SimpleNamespace(ROOT=tmp_path,json_response=lambda handler,data,status=200:results.append((status,data)))
    monkeypatch.setattr(handlers,'_get_main_module',lambda:main)

    class FakeHandler:
        path='/api/v2/telemetry/import-source'
        headers={'Content-Length':str(len(raw)),
                 'X-AAS-Import-Metadata':base64.b64encode(json.dumps(m).encode()).decode()}
        rfile=io.BytesIO(raw)
        def _handle_auth_post(self,*args):return False
        def _require_auth(self,*args,**kwargs):return True

    handlers.DashboardHandler._dispatch_POST(FakeHandler())
    assert results[0][0]==200 and results[0][1]['format']=='csv.gz'
    assert counts(target)==[0,0,0,0]


def test_binary_ui_keeps_old_layout_and_preview_commit_contract():
    root=Path(__file__).resolve().parents[1]/'web'/'visualization'
    html=(root/'import.html').read_text('utf8')
    js=(root/'telemetry-v2-import.js').read_text('utf8')
    assert 'id="v2ImpSqliteTableField" hidden' in html
    assert 'id="v2ImpFile"' in html and '.csv,.csv.gz,.gz,.sqlite,.sqlite3,.db' in html
    assert 'id="v2ImpCsvFormatField"' in html and 'id="v2ImpCsvFormatConfirmed"' in html
    assert 'v2ImpMappingField' in html and 'v2ImpMappingPreview' in html
    assert "'/api/v2/telemetry/import-source'" in js
    assert "operation:'inspect'" in js and "operation:'preview'" in js and "operation:'commit'" in js


def test_plain_csv_binary_import_uses_same_v2_contract_and_hash(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed,label\n0,1,0\n1,2,0\n2,3,0\n'
    info=source_operation(path,raw,{'operation':'inspect','filename':'healthy.csv'})
    assert info['format']=='csv' and info['has_labels'] is True
    cfg=meta('healthy.csv')
    cfg['config']['role']='train'
    before=counts(path)
    preview=source_operation(path,raw,cfg)
    assert preview['status']=='preview' and counts(path)==before
    saved=source_operation(path,raw,{**cfg,'operation':'commit'})
    assert saved['source_format']=='csv' and saved['role']=='train' and saved['row_count']==3
    with sqlite3.connect(path) as db:
        digest,size=db.execute('SELECT source_sha256,source_bytes FROM v2_sources').fetchone()
    assert (digest,size)==(hashlib.sha256(raw).hexdigest(),len(raw))


def test_plain_csv_unlabeled_requires_explicit_role_confirmation(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed\n0,1\n1,2\n'
    cfg=meta('mylocal.csv')
    assert source_operation(path,raw,{'operation':'inspect','filename':'mylocal.csv'})['has_labels'] is False
    cfg['config']['role']='train'
    with pytest.raises(ValueError,match='确认'):
        source_operation(path,raw,cfg)
    cfg['config']['all_normal_confirmed']=True
    assert source_operation(path,raw,{**cfg,'operation':'commit'})['label_status']=='confirmed_normal'


def test_plain_csv_rejects_unsafe_name_invalid_mapping_and_encoding(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed\n0,1\n1,2\n'
    with pytest.raises(ValueError):
        source_operation(path,raw,{'operation':'inspect','filename':'../../evil.csv'})
    with pytest.raises(ValueError):
        source_operation(path,raw,{'operation':'inspect','filename':'raw.zip'})
    with pytest.raises(ValueError):
        source_operation(path,raw.replace(b'2\n',b'\xff\n'),{'operation':'inspect','filename':'unsafe.csv'})
    cfg=meta('good.csv')
    cfg['config']['column_metadata']={'wrong':{'subsystem':'ADCS','device':'RW1','variable':'x','unit':'rad/s'}}
    with pytest.raises(ValueError):
        source_operation(path,raw,cfg)
    assert counts(path)==[0,0,0,0]


def test_excel_gb18030_semicolon_inspect_explicit_preview_commit_retains_raw_hash(tmp_path):
    path = dest(tmp_path)
    # A common Windows/Excel export: Chinese notes, semicolon separator and CRLF.
    raw = ('time_s;adcs.rw.speed;label;note\r\n'
           '0;1.5;0;"中文;说明"\r\n'
           '1;2.5;1;"测试,甲"\r\n').encode('gb18030')
    info = source_operation(path, raw, {'operation': 'inspect', 'filename': '现场.csv',
                                        'csv_encoding': 'auto', 'csv_delimiter': 'auto'})
    assert info['columns'] == ['time_s', 'adcs.rw.speed', 'label', 'note']
    assert (info['csv_encoding'], info['csv_delimiter']) == ('gb18030', ';')
    assert info['format_review_required'] is True
    assert info['numeric_columns'] == ['adcs.rw.speed']
    assert info['sampled_rows'] == 2
    cfg = meta('现场.csv', csv_encoding='gb18030', csv_delimiter=';')
    cfg['config']['selected_columns'] = ['adcs.rw.speed']
    assert source_operation(path, raw, cfg)['row_count'] == 2
    assert counts(path) == [0, 0, 0, 0]
    saved = source_operation(path, raw, {**cfg, 'operation': 'commit'})
    assert saved['row_count'] == 2 and saved['column_count'] == 1
    assert counts(path) == [1, 1, 1, 2]
    with sqlite3.connect(path) as conn:
        digest, size = conn.execute('SELECT source_sha256,source_bytes FROM v2_sources').fetchone()
    assert (digest, size) == (hashlib.sha256(raw).hexdigest(), len(raw))


def test_bom_tab_delimited_csv_does_not_change_user_values_or_provenance(tmp_path):
    path = dest(tmp_path)
    raw = ('time_s\tadcs.rw.speed\tlabel\r\n0\t3\t0\r\n1\t4\t1\r\n').encode('utf-8-sig')
    info = source_operation(path, raw, {'operation':'inspect', 'filename':'tab.csv',
                                        'csv_encoding':'auto', 'csv_delimiter':'auto'})
    assert info['csv_encoding'] == 'utf-8' and info['csv_delimiter'] == '\t'
    cfg = meta('tab.csv', csv_encoding='utf-8', csv_delimiter='\t')
    assert source_operation(path, raw, {**cfg, 'operation':'commit'})['row_count'] == 2
    with sqlite3.connect(path) as conn:
        values = conn.execute('SELECT value_num FROM v2_samples ORDER BY rowid').fetchall()
        digest = conn.execute('SELECT source_sha256 FROM v2_sources').fetchone()[0]
    assert values == [(3.0,), (4.0,)] and digest == hashlib.sha256(raw).hexdigest()


def test_gzip_gb18030_tab_source_works_with_same_explicit_contract(tmp_path):
    path = dest(tmp_path)
    raw = gzip.compress(('time_s\tadcs.rw.speed\tlabel\tnote\n'
                         '0\t9\t0\t现场采样\n1\t10\t1\t确认\n').encode('gb18030'))
    info = source_operation(path, raw, {'operation':'inspect','filename':'local.csv.gz',
                                        'csv_encoding':'auto','csv_delimiter':'auto'})
    assert info['format'] == 'csv.gz'
    assert (info['csv_encoding'], info['csv_delimiter']) == ('gb18030', '\t')
    assert info['numeric_columns'] == ['adcs.rw.speed']
    cfg = meta('local.csv.gz', csv_encoding='gb18030', csv_delimiter='\t')
    cfg['config']['selected_columns'] = ['adcs.rw.speed']
    done = source_operation(path, raw, {**cfg, 'operation':'commit'})
    assert done['source_format'] == 'csv.gz' and done['row_count'] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT source_sha256 FROM v2_sources').fetchone()[0] == hashlib.sha256(raw).hexdigest()


def test_numeric_suggestion_is_advisory_for_initially_empty_sensor_column(tmp_path):
    path = dest(tmp_path)
    raw = ('time_s,adcs.rw.speed,label\n' +
           ''.join(f'{frame},,0\n' for frame in range(50)) +
           '50,7.5,0\n').encode('utf-8')
    info = source_operation(path, raw, {'operation':'inspect','filename':'sparse.csv',
                                        'csv_encoding':'auto','csv_delimiter':'auto'})
    assert info['sampled_rows'] == 50 and info['numeric_columns'] == []
    cfg = meta('sparse.csv')
    cfg['config']['selected_columns'] = ['adcs.rw.speed']
    preview = source_operation(path, raw, cfg)
    assert preview['row_count'] == 51
    assert preview['missing_by_column'] == {'adcs.rw.speed':50}
    assert counts(path) == [0,0,0,0]


@pytest.mark.parametrize('bad_format', [
    {'csv_encoding':'utf-16'}, {'csv_delimiter':'|'},
    {'csv_encoding':'auto'}, {'csv_delimiter':'auto'},
])
def test_csv_preview_rejects_unconfirmed_or_unsupported_format(tmp_path, bad_format):
    path = dest(tmp_path)
    raw = b'time_s,adcs.rw.speed,label\n0,1,0\n1,2,1\n'
    with pytest.raises(ValueError, match='编码|分隔符|确认'):
        source_operation(path, raw, {**meta('raw.csv'), **bad_format, 'operation':'commit'})
    assert counts(path) == [0, 0, 0, 0]


def test_csv_wrong_format_or_malformed_fields_does_not_partially_import(tmp_path):
    path = dest(tmp_path)
    raw = b'time_s;adcs.rw.speed;label\n0;1;0\n1;2;1\n'
    with pytest.raises(ValueError):
        source_operation(path, raw, {**meta('wrong.csv'), 'operation':'commit'})
    bad = b'time_s;adcs.rw.speed;label\n0;1;0\n1;2;1;unexpected\n'
    with pytest.raises(ValueError):
        source_operation(path, bad, {**meta('bad.csv'), 'csv_delimiter':';',
                                     'operation':'commit'})
    assert counts(path) == [0, 0, 0, 0]


def test_direct_training_csv_selects_numeric_columns_ignores_export_metadata_and_binds_group(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed,label,note,aas_frame,aas_injected_fault\n0,1,0,hello,1,0\n1,2,0,world,2,0\n'
    cfg=meta('from-simulation.csv')
    cfg['config'].update(role='train',selected_columns=['adcs.rw.speed'],bind_digest_group=True)
    preview=source_operation(path,raw,cfg)
    assert preview['column_count']==1
    saved=source_operation(path,raw,{**cfg,'operation':'commit'})
    assert saved['row_count']==2
    with sqlite3.connect(path) as db:
        group,split=db.execute('SELECT group_id,dataset_split FROM v2_import_context').fetchone()
    assert len(group)==24 and split=='train'
    # A renamed copy of the very same CSV must not masquerade as a new test group.
    cfg['config']['role']='test'
    with pytest.raises(ValueError):
        source_operation(path,raw,{**cfg,'filename':'rename.csv','operation':'commit'})
    assert counts(path)==[1,1,1,2]


def test_direct_upload_csv_is_usable_as_v2_training_and_evaluation_runs(tmp_path):
    from scripts.server.v2_run_dataset import catalog
    path=dest(tmp_path)
    train_csv=b'time_s,adcs.rw.speed,label\n'+b''.join(f'{i},{i+10},0\n'.encode() for i in range(16))
    test_csv=b'time_s,adcs.rw.speed,label\n'+b''.join(
        f'{i},{i+20},{1 if i>=6 else 0}\n'.encode() for i in range(16))
    cfg=meta('train.csv')
    cfg['config'].update(role='train',bind_digest_group=True,selected_columns=['adcs.rw.speed'])
    train=source_operation(path,train_csv,{**cfg,'operation':'commit'})
    cfg['config'].update(role='test',namespace='external_test',time_reference='another-run')
    test=source_operation(path,test_csv,{**cfg,'filename':'test.csv','operation':'commit'})
    result=catalog(path)
    assert {r['source_id'] for r in result['runs'] if r['allowed_role']=='train'}=={train['source_id']}
    assert {r['source_id'] for r in result['runs'] if r['allowed_role']=='test'}=={test['source_id']}
    assert any(x['feature_key']=='ADCS.RW1.speed' for x in result['features'])


def test_plain_csv_preview_and_commit_require_explicit_format_confirmation(tmp_path):
    path = dest(tmp_path)
    raw = 'time_s;adcs.rw.speed;label\n0;1.0;0\n1;2.0;1\n'.encode('utf-8')
    cfg = meta('explicit.csv', csv_delimiter=';')
    cfg.pop('format_confirmed')
    with pytest.raises(ValueError, match='明确确认'):
        source_operation(path, raw, cfg)
    cfg['format_confirmed'] = True
    preview = source_operation(path, raw, cfg)
    assert preview['status'] == 'preview'
    assert counts(path) == [0, 0, 0, 0]
    saved = source_operation(path, raw, {**cfg, 'operation': 'commit'})
    assert saved['row_count'] == 2
    assert counts(path) == [1, 1, 1, 2]


def test_plain_csv_http_route_uses_confirmed_binary_format_metadata(tmp_path, monkeypatch):
    import base64
    import json
    import io
    from types import SimpleNamespace
    from scripts.server import handlers

    dataset = tmp_path / 'datasets'
    dataset.mkdir()
    target = dataset / 'aas_telemetry_v2.sqlite'
    with sqlite3.connect(target) as db:
        create_v2(db)
    raw = ('time_s;adcs.rw.转速;label\n0;1.0;0\n1;2.0;1\n').encode('gb18030')
    results = []
    main = SimpleNamespace(ROOT=tmp_path, json_response=lambda handler, data, status=200: results.append((status, data)))
    monkeypatch.setattr(handlers, '_get_main_module', lambda: main)

    def dispatch(meta):
        body = json.dumps(meta, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Length': str(len(raw)),
                   'X-AAS-Import-Metadata': base64.b64encode(body).decode('ascii')}
        class FakeHandler:
            path = '/api/v2/telemetry/import-source'
            rfile = io.BytesIO(raw)
            def __init__(self): self.headers = headers
            def _handle_auth_post(self, *args): return False
            def _require_auth(self, *args, **kwargs): return True
        handlers.DashboardHandler._dispatch_POST(FakeHandler())
        return results[-1]

    inspect_status, inspected = dispatch({'filename': '现场.csv', 'operation': 'inspect',
                                          'csv_encoding': 'auto', 'csv_delimiter': 'auto'})
    assert inspect_status == 200
    assert inspected['csv_encoding'] == 'gb18030' and inspected['csv_delimiter'] == ';'
    cfg = meta('现场.csv', csv_encoding='gb18030', csv_delimiter=';', format_confirmed=True)
    cfg['config']['column_metadata'] = {'adcs.rw.转速': {'subsystem':'ADCS','device':'RW1','variable':'speed','unit':'rad/s'}}
    preview_status, preview_result = dispatch(cfg)
    assert preview_status == 200 and preview_result['status'] == 'preview'
    assert counts(target) == [0, 0, 0, 0]
    commit_status, committed = dispatch({**cfg, 'operation': 'commit'})
    assert commit_status == 200 and committed['row_count'] == 2
    assert counts(target) == [1, 1, 1, 2]
    missing_confirmation = dict(cfg)
    missing_confirmation.pop('format_confirmed')
    rejected_status, rejected = dispatch(missing_confirmation)
    assert rejected_status == 400 and '确认' in rejected['error']
    assert counts(target) == [1, 1, 1, 2]


def test_history_import_browser_csv_format_workflow():
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('Node 不可用')
    result = subprocess.run(['node', 'tests/telemetry_v2_import_format_ui.cjs'],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


def test_csv_inspection_reports_bounded_quality_and_role_candidates(tmp_path):
    raw='采样时刻,adcs.rw.speed,fault_state,说明\n0,1,0,正常\n1,,1,缺测\n1,NaN,X,异常\n'.encode()
    info=source_operation(dest(tmp_path),raw,{'operation':'inspect','filename':'quality.csv', 'time_column':'采样时刻','label_column':'fault_state'})
    assert info['quality']['row_count']==3
    assert info['quality']['duplicate_times']==1
    assert info['quality']['label_counts']=={'0':1,'1':1,'unknown':1}
    assert info['quality']['nonfinite_by_column']=={'adcs.rw.speed':1}
    assert info['quality']['missing_by_column']=={'adcs.rw.speed':1}
    assert '采样时刻' in info['time_candidates']
    assert 'fault_state' in info['label_candidates']


def test_selected_nonstandard_time_and_truth_label_columns_reach_database(tmp_path):
    path=dest(tmp_path)
    raw='采样时刻,adcs.rw.speed,fault_state,说明\n0,1,0,ok\n1,2,1,fault\n'.encode()
    config=meta('custom.csv')
    config['config'].update(time_column='采样时刻',label_column='fault_state',
                            selected_columns=['adcs.rw.speed'])
    result=source_operation(path,raw,config)
    assert result['row_count']==2 and result['label_status']=='provided'
    saved=source_operation(path,raw,{**config,'operation':'commit'})
    assert saved['row_count']==2
    with sqlite3.connect(path) as conn:
        assert [x[0] for x in conn.execute('SELECT label_int FROM v2_samples ORDER BY time_sort_num')] == [0,1]


def test_explicit_no_label_requires_confirmation_even_if_source_has_label_column(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed,label\n0,1,0\n1,2,0\n'
    config=meta('nolabel.csv')
    config['config'].update(time_column='time_s',label_column='',selected_columns=['adcs.rw.speed'])
    config['config']['role']='train'
    config['config'].pop('all_normal_confirmed',None)
    with pytest.raises(ValueError,match='确认'):
        source_operation(path,raw,config)
    assert counts(path)==[0,0,0,0]


def test_time_role_cannot_be_selected_as_measurement_or_missing_header(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,speed,label\n0,1,0\n1,2,1\n'
    config=meta('wrong.csv')
    config['config'].update(selected_columns=['time_s'],time_column='time_s')
    with pytest.raises(ValueError,match='时间/标签'):
        source_operation(path,raw,config)
    config['config'].update(selected_columns=['speed'],time_column='absent')
    with pytest.raises(ValueError,match='时间列'):
        source_operation(path,raw,config)


def test_ignoring_existing_fault_truth_does_not_qualify_it_for_normal_training(tmp_path):
    path=dest(tmp_path)
    raw=b'time_s,adcs.rw.speed,label\n0,1,0\n1,2,1\n'
    config=meta('source-fault.csv')
    config['config'].update(time_column='time_s',label_column='',
                            selected_columns=['adcs.rw.speed'],role='train',
                            all_normal_confirmed=True)
    with pytest.raises(ValueError, match='已存在故障标签'):
        source_operation(path,raw,config)
    assert counts(path)==[0,0,0,0]


def test_csv_inspect_locates_incomplete_row_instead_of_hiding_quality_report(tmp_path):
    raw=b'time_s,speed,label\n0,1,0\n1,2\n2,3,1\n'
    path=dest(tmp_path)
    report=source_operation(path,raw,{'operation':'inspect','filename':'uneven.csv'})
    quality=report['quality']
    assert quality['row_count']==3
    assert any(x['code']=='column_count' and x['line']==3 and x['frame']==2 for x in quality['issues'])
    cfg=meta('uneven.csv')
    cfg['config']['column_metadata']={'speed':{'subsystem':'ADCS','device':'RW1','variable':'speed','unit':'rad/s'}}
    with pytest.raises(ValueError,match='列数'):
        source_operation(path,raw,cfg)
