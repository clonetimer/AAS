"""Authenticated local AAS model transfer, never arbitrary pickle upload."""
import io
import json
import zipfile
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.server import v2_model_bundle as bundles


def make_model(root):
    folder=root/'models'/'model_aas_test_01'
    folder.mkdir(parents=True)
    config={'experiment_id':'aas_test_01','algorithm':'zscore','dataset':'aas_telemetry_v2.sqlite',
      'table_name':'v2_channels','config':{'data_contract':{
        'version':7,'v2_run_selection':{'source_ids':[1],'feature_keys':['ADCS.RW.speed']},
        'v2_run_manifest':{'runs':[{'group_id':'unit-test'}]},
        'model_input_scaling':{'enabled':True}}}}
    (folder/'config.json').write_text(json.dumps(config),encoding='utf-8')
    (folder/'metrics.json').write_text(json.dumps({'accuracy':0.85}),encoding='utf-8')
    (folder/'model.pkl').write_bytes(b'not-a-real-pickle; tests must not deserialize')
    return {'experiment_id':'aas_test_01','algorithm':'zscore','file_path':'models/model_aas_test_01',
            'dataset':'aas_telemetry_v2.sqlite','table_name':'v2_channels','model_name':'my baseline'}


def test_model_package_export_and_verify_without_deserialization(tmp_path):
    model=make_model(tmp_path)
    raw=bundles.export_model_bundle(tmp_path, model)
    assert len(raw)>100
    verified=bundles.verify_model_bundle(tmp_path,raw)
    assert verified['config']['algorithm']=='zscore'
    assert verified['files']['model.pkl'].startswith(b'not-a-real-pickle')
    assert verified['manifest']['model_name']=='my baseline'
    assert (tmp_path/'configs'/'aas_model_bundle.key').is_file()
    with pytest.raises(ValueError):
        bundles.verify_model_bundle(tmp_path, b'garbage')


def test_changed_model_or_manifest_rejected_before_any_model_load(tmp_path):
    raw=bundles.export_model_bundle(tmp_path,make_model(tmp_path))
    with zipfile.ZipFile(io.BytesIO(raw)) as src:
        entries={name:src.read(name) for name in src.namelist()}
    entries['model.pkl']=b'evil_payload_that_must_never_be_loaded'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as dest:
        for name,body in entries.items():dest.writestr(name,body)
    with pytest.raises(ValueError,match='校验|签名|摘要'):
        bundles.verify_model_bundle(tmp_path,out.getvalue())
    with pytest.raises(ValueError):
        bundles.verify_model_bundle(tmp_path.parent,out.getvalue())


def test_safely_reject_signed_package_with_malformed_config_contract(tmp_path):
    model=make_model(tmp_path)
    path=tmp_path/model['file_path']/'config.json'
    config=json.loads(path.read_text('utf8'))
    config['config']=['unexpected-array']
    path.write_text(json.dumps(config),encoding='utf8')
    signed=bundles.export_model_bundle(tmp_path,model)
    with pytest.raises(ValueError,match='训练契约'):
        bundles.verify_model_bundle(tmp_path,signed)


def test_zip_path_traversal_and_non_archive_formats_are_rejected(tmp_path):
    bundles.export_model_bundle(tmp_path,make_model(tmp_path))
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('../model.pkl',b'bad')
    with pytest.raises(ValueError):
        bundles.verify_model_bundle(tmp_path,stream.getvalue())
    with pytest.raises(ValueError):
        bundles.verify_model_bundle(tmp_path,b'\x80\x05pickle')


def test_install_creates_new_model_version_only_after_signature_validation(tmp_path):
    model=make_model(tmp_path)
    raw=bundles.export_model_bundle(tmp_path,model)
    recorded=[]
    class Fake:
        ROOT=tmp_path
        def create_model(self,*args):recorded.append(args)
        def get_model_by_experiment_id(self,experiment_id):return {'id':17,'experiment_id':experiment_id}
    with patch('scripts.core.model_manager.validate_saved_model',return_value=SimpleNamespace(score=lambda rows: rows)) as validation:
        saved=bundles.install_model_bundle(Fake(),raw,'imported model')
        assert validation.called
    assert saved['id']==17 and len(recorded)==1
    directory=tmp_path/recorded[0][6]
    assert directory.is_dir() and directory.parent==(tmp_path/'models')
    config=json.loads((directory/'config.json').read_text('utf-8'))
    assert config['experiment_id']==saved['experiment_id']!=model['experiment_id']
    assert (directory/'model.pkl').read_bytes()==b'not-a-real-pickle; tests must not deserialize'
    assert (tmp_path/'models'/'model_aas_test_01'/'config.json').is_file()


def test_http_model_import_rejects_unsigned_zip(tmp_path):
    from scripts import visualization_server as app
    from scripts.server.home_auth import HomeAuth
    from unittest.mock import patch
    auth=HomeAuth(tmp_path)
    with patch.object(app,'ROOT',tmp_path),patch.object(app,'HOME_AUTH',auth):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.DashboardHandler)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        def post(path,raw,headers=None):
            with urlopen(Request(origin+path,data=raw,headers=headers or {'Content-Type':'application/json'}),timeout=8) as response:
                return json.load(response),response.headers.get('Set-Cookie','')
        try:
            post('/api/auth/setup',json.dumps({'username':'admin','password':'strong-password-for-test'}).encode())
            _,cookie=post('/api/auth/login',json.dumps({'username':'admin','password':'strong-password-for-test'}).encode())
            from base64 import b64encode
            headers={'Cookie':cookie.split(';',1)[0],
                     'X-AAS-Model-Name-B64':b64encode('malicious'.encode()).decode()}
            with pytest.raises(HTTPError) as err:
                post('/api/v2/models/import-bundle',b'not-a-signed-archive',headers)
            assert err.value.code==400
            assert not (tmp_path/'models').exists()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
