import json
from pathlib import Path
from types import SimpleNamespace

from scripts.server.post_models import handle_model_post
from scripts.server.v2_user_csv_workspace import database_reference


def test_user_csv_model_can_publish(tmp_path):
    model_dir=tmp_path/'models'/'model_demo'
    model_dir.mkdir(parents=True)
    saved={'config':{'data_contract':{'version':7,
        'v2_run_selection':{'coordinate_mode':'frame','feature_keys':['S.D.x']},
        'coordinate_contract':{'mode':'frame','frame_origin':1,'alignment':'frame_index',
                               'frame_sequence_policy':'independent'},
        'data_source_provenance':{'type':'user_csv','database_ref':database_reference()}}}}
    calls=[]
    main=SimpleNamespace(ROOT=tmp_path,
        resolve_managed_model_dir=lambda *_args,**_kwargs:model_dir,
        load_model_config=lambda *_args:saved,
        create_model=lambda **kwargs:calls.append(kwargs) or {'status':'success'},
        json_response=lambda _handler,data,status=200:(status,data))
    payload={'experiment_id':'demo','model_name':'frame-model','algorithm':'three_sigma',
        'dataset':database_reference(),'table_name':'v2_channels','file_path':str(model_dir)}
    response=handle_model_post(None,SimpleNamespace(path='/api/models'),main,
                               json.dumps(payload).encode())
    assert response[0]==200 and calls[0]['dataset']==database_reference()
