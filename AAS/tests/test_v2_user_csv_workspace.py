from pathlib import Path
import pytest
from scripts.server import v2_user_csv_workspace as ws

def test_persistent_import_list_and_read(tmp_path):
    raw=b'time_s,temp,label\\n0,1,0\\n1,2,1\\n'
    item=ws.import_csv(tmp_path,raw,'mine.csv','train',{'rows':2},format_confirmed=True)
    assert ws.database_path(tmp_path).is_file()
    assert ws.list_datasets(tmp_path,'train')[0]['id']==item['id']
    assert ws.get_dataset(tmp_path,item['id'],include_bytes=True)['raw_bytes']==raw
    assert ws.list_datasets(tmp_path,'test')==[]

def test_reject_unconfirmed_or_wrong_role(tmp_path):
    with pytest.raises(ValueError): ws.import_csv(tmp_path,b'x','x.csv','train',{},format_confirmed=False)
    with pytest.raises(ValueError): ws.import_csv(tmp_path,b'x','x.csv','history',{},format_confirmed=True)

def test_task_reference_blocks_delete_until_released(tmp_path):
    item=ws.import_csv(tmp_path,b't,x\\n0,1\\n','x.csv','test',{},format_confirmed=True)
    ws.bind_task(tmp_path,'task-1',item['id'])
    with pytest.raises(RuntimeError): ws.delete_dataset(tmp_path,item['id'],confirm=True)
    ws.release_task(tmp_path,'task-1')
    assert ws.delete_dataset(tmp_path,item['id'],confirm=True)['deleted'] is True

def test_historical_database_is_not_touched(tmp_path):
    historical=tmp_path/'datasets'/'aas_telemetry_v2.sqlite'
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b'protected-historical-db')
    before=historical.read_bytes()
    ws.import_csv(tmp_path,b't,x\\n0,1\\n','x.csv','train',{},format_confirmed=True)
    assert historical.read_bytes()==before
    assert ws.database_path(tmp_path).parent.name=='user_csv_workspaces'
