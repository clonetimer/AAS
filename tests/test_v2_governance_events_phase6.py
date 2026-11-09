"""V2 segment governance and independent event annotations, no legacy data mode."""
from pathlib import Path
import sqlite3
import pytest
from urllib.parse import urlparse
import json

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_import import import_csv
from scripts.server.telemetry_v2_access import query_charts,list_channels
from scripts.server.telemetry_v2_training import load_matrix
from scripts.server.telemetry_v2_governance import (
    list_segments,set_segment_status,create_event,retract_event,list_events,list_audit)


def store(tmp_path,role='test',time_domain='datetime_utc'):
    path=tmp_path/'datasets'/'aas_telemetry_v2.sqlite';path.parent.mkdir()
    with sqlite3.connect(path) as conn:create_v2(conn)
    default={'namespace':'mission_A','model':'GF1','satellite_instance':'sat_01','subsystem':'ADCS',
             'device':'RW1','variable':'speed','unit':'rpm','role':role,'filename':'part.csv',
             'time_domain':time_domain,
             'csv':'timestamp,value,label\n2026-01-01T00:00:00Z,1,0\n2026-01-01T00:00:01Z,3,1\n2026-01-01T00:00:02Z,4,0\n'}
    if role=='train':default['csv']=default['csv'].replace(',3,1',',3,0')
    if time_domain=='numeric_unspecified':
        default.update(time_reference='mission_A_elapsed',time_unit='ms',csv='timestamp,value,label\n0,1,0\n1000,3,1\n2000,4,0\n')
    ing=import_csv(path,default,commit=True)
    return path, default, ing


def test_quarantine_is_reversible_atomic_and_seen_by_reader(tmp_path):
    path,raw,item=store(tmp_path)
    cid=item['channel_id'];sid=item['segment_id']
    assert list_segments(path,channel_id=cid)['items'][0]['active']==1
    with pytest.raises(ValueError,match='confirm'):
        set_segment_status(path,{'segment_id':sid,'active':False,'expected_revision':0,'reason':'bad'})
    removed=set_segment_status(path,{'segment_id':sid,'active':False,'expected_revision':0,'reason':'bad', 'confirm':True})
    assert removed['revision']==1 and removed['active'] is False
    assert list_channels(path,role='test')['count']==0
    with pytest.raises(ValueError,match='没有已确认用途'):
        load_matrix(path,channel_ids=[cid],role='test')
    assert query_charts(path,role='test',channel_ids=[cid])['series'][0]['matched_count']==0
    with pytest.raises(ValueError,match='版本'):
        set_segment_status(path,{'segment_id':sid,'active':True,'expected_revision':0,'reason':'old','confirm':True})
    restored=set_segment_status(path,{'segment_id':sid,'active':True,'expected_revision':1,'reason':'rechecked','confirm':True})
    assert restored['revision']==2
    assert list_channels(path,role='test')['count']==1
    assert query_charts(path,role='test',channel_ids=[cid])['series'][0]['matched_count']==3
    assert [a['action'] for a in list_audit(path)['items']]==['restore','quarantine']
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==3
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_events_no_sample_modifications_and_overlap_guard(tmp_path):
    path,raw,item=store(tmp_path)
    cid=item['channel_id']
    request={'channel_id':cid,'role':'test','start':'2026-01-01T00:00:01Z',
             'end':'2026-01-01T00:00:02Z','label':1,'fault_type':'RW_FRICTION','note':'expert review','confirm':True}
    event=create_event(path,request)
    assert event['status']=='created'
    with pytest.raises(ValueError,match='重叠'):
        create_event(path,{**request,'fault_type':'OTHER'})
    with pytest.raises(ValueError,match='没有有效采样'):
        create_event(path,{**request,'start':'2026-01-02T00:00:00Z','end':'2026-01-02T00:00:01Z'})
    with pytest.raises(ValueError,match='晚于'):
        create_event(path,{**request,'start':request['end'],'end':request['start']})
    with pytest.raises(ValueError,match='confirm'):
        retract_event(path,{'event_id':event['event_id'],'expected_revision':0,'reason':'x'})
    assert len(list_events(path,channel_id=cid,role='test')['items'])==1
    retract_event(path,{'event_id':event['event_id'],'expected_revision':0,'reason':'duplicate','confirm':True})
    assert len(list_events(path,channel_id=cid,role='test')['items'])==0
    assert len(list_events(path,channel_id=cid,role='test',include_retracted=True)['items'])==1
    assert create_event(path,request)['status']=='created'
    with sqlite3.connect(path) as conn:
        assert [r[0] for r in conn.execute('SELECT label_int FROM v2_samples ORDER BY id')]==[0,1,0]
        assert [r[0] for r in conn.execute('SELECT action FROM v2_governance_audit')]==['create','retract','create']


def test_train_faults_rejected_and_numeric_clock_required(tmp_path):
    path,_,item=store(tmp_path,role='train')
    request={'channel_id':item['channel_id'],'role':'train','start':'2026-01-01T00:00:00Z',
             'end':'2026-01-01T00:00:02Z','label':1,'fault_type':'F','confirm':True}
    with pytest.raises(ValueError,match='训练用途'):
        create_event(path,request)
    assert create_event(path,{**request,'label':0})['status']=='created'
    other=tmp_path/'second';other.mkdir()
    path,_,item=store(other,time_domain='numeric_unspecified')
    res=create_event(path,{'channel_id':item['channel_id'],'role':'test',
                           'start':'1000','end':'2000','label':1,'fault_type':'bias','confirm':True})
    assert list_events(path,channel_id=item['channel_id'],role='test')['items'][0]['time_unit']=='ms'
    assert res['event_id']>0


def test_http_v2_only_management_routes(tmp_path):
    from scripts.server.data_routes import GET_DATA_ROUTES,POST_DATA_ROUTES
    path,_,item=store(tmp_path)
    class Main:
        ROOT=tmp_path
        @staticmethod
        def json_response(handler,result,status=200):return status,result
    sid=item['segment_id'];cid=item['channel_id']
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/segments'](None,urlparse('/api/v2/telemetry/segments?channel_id='+str(cid)),Main)
    assert status==200 and result['items'][0]['segment_id']==sid
    status,result=POST_DATA_ROUTES['/api/v2/telemetry/events/create'](None,urlparse('/api/v2/telemetry/events/create'),Main,
        json.dumps({'channel_id':cid,'role':'test','start':'2026-01-01T00:00:01Z','end':'2026-01-01T00:00:02Z',
                   'label':1,'fault_type':'FRICTION','confirm':True}).encode())
    assert status==200 and result['status']=='created'
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/events'](None,urlparse(f'/api/v2/telemetry/events?channel_id={cid}&role=test'),Main)
    assert status==200 and len(result['items'])==1
    status,result=POST_DATA_ROUTES['/api/v2/telemetry/segment/status'](None,urlparse('/api/v2/telemetry/segment/status'),Main,
        json.dumps({'segment_id':sid,'active':False,'expected_revision':0,'reason':'exclude','confirm':True}).encode())
    assert status==200 and result['active'] is False
    status,result=GET_DATA_ROUTES['/api/v2/telemetry/governance/audit'](None,urlparse('/api/v2/telemetry/governance/audit'),Main)
    assert status==200 and len(result['items'])==2


def test_read_legacy_v2_schema_before_governance_exists(tmp_path):
    path,_,item=store(tmp_path)
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_segment_governance'").fetchone()
    assert list_segments(path,channel_id=item['channel_id'])['items'][0]['active']==1
    payload=list_events(path,channel_id=item['channel_id'],role='test')
    assert payload['items']==[] and payload['run_annotations']==[]
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_fault_events'").fetchone()


def test_v2_event_metrics_use_detector_decisions_without_changing_sample_labels(tmp_path):
    from scripts.server.telemetry_v2_governance import evaluate_v2_events
    path,_,item=store(tmp_path)
    cid=item['channel_id']
    create_event(path,{'channel_id':cid,'role':'test','start':'2026-01-01T00:00:01Z',
                       'end':'2026-01-01T00:00:02Z','label':1,'fault_type':'RW_FRICTION','confirm':True})
    result={'rows':[{'timestamp':f'2026-01-01T00:00:0{i}Z','label':int(i==1)} for i in range(3)],
            'pred_labels':[0,1,0]}
    old=json.dumps(result,sort_keys=True)
    evaluated=evaluate_v2_events(path,result,channel_ids=[cid])
    assert evaluated['event_overlay']['total_intervals']==1
    metrics=evaluated['event_metrics']
    assert metrics['status']=='ok'
    assert metrics['fault_events_detected']==1
    assert json.dumps(result,sort_keys=True)==old
    assert evaluated['event_overlay']['items'][0]['source_table']==f'v2:channel:{cid}'


def test_v2_numeric_ms_event_delay_is_seconds(tmp_path):
    from scripts.server.telemetry_v2_governance import evaluate_v2_events
    path,_,item=store(tmp_path,time_domain='numeric_unspecified')
    cid=item['channel_id']
    create_event(path,{'channel_id':cid,'role':'test','start':'1000','end':'2000','label':1,
                       'fault_type':'bias','confirm':True})
    result={'rows':[{'timestamp':str(i)} for i in (0,1000,2000)], 'pred_labels':[0,1,0]}
    metrics=evaluate_v2_events(path,result,channel_ids=[cid])['event_metrics']
    assert metrics['status']=='ok'
    assert metrics['fault_events_detected']==1
    assert metrics['median_first_alarm_delay_seconds']==0
    assert result['rows'][1]['timestamp']=='1000'


def test_restore_rejects_conflicting_replacement_without_losing_rows(tmp_path):
    path,raw,first=store(tmp_path)
    sid=first['segment_id'];cid=first['channel_id']
    set_segment_status(path,{'segment_id':sid,'active':False,'expected_revision':0,
                             'reason':'suspected bad values','confirm':True})
    new_csv='timestamp,value,label\n2026-01-01T00:00:00Z,99,0\n2026-01-01T00:00:01Z,3,1\n2026-01-01T00:00:02Z,4,0\n'
    replacement=import_csv(path,{**raw,'filename':'replacement.csv','csv':new_csv},commit=True)
    assert replacement['channel_id']==cid
    with pytest.raises(ValueError,match='冲突时间戳'):
        set_segment_status(path,{'segment_id':sid,'active':True,'expected_revision':1,
                                 'reason':'attempt restore','confirm':True})
    assert query_charts(path,role='test',channel_ids=[cid])['series'][0]['points'][0]['value']==99
    old=list_segments(path,channel_id=cid)['items']
    assert next(x for x in old if x['segment_id']==sid)['active']==0
    assert list_audit(path)['items'][0]['action']=='quarantine'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==6


def test_live_http_phase6_routes_with_isolated_root(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from tests.http_test_handler import AuthorizedTestHandler
    import scripts.visualization_server as main

    path,raw,item=store(tmp_path)
    monkeypatch.setattr(main,'ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def api(endpoint, payload=None):
        body=json.dumps(payload).encode('utf-8') if payload is not None else None
        req=Request(base+endpoint,data=body,method='POST' if body else 'GET',
                    headers={'Content-Type':'application/json'} if body else {})
        try: response=urlopen(req,timeout=5)
        except HTTPError as exc:response=exc
        with response:return response.status,json.loads(response.read())
    try:
        status,body=api('/api/v2/telemetry/segments?channel_id='+str(item['channel_id']))
        assert status==200 and len(body['items'])==1
        status,body=api('/api/v2/telemetry/events/create',{
            'channel_id':item['channel_id'],'role':'test','start':'2026-01-01T00:00:01Z',
            'end':'2026-01-01T00:00:02Z','label':1,'fault_type':'FRICTION','confirm':True})
        assert status==200 and body['status']=='created'
        status,body=api('/api/v2/telemetry/events?channel_id='+str(item['channel_id'])+'&role=test')
        assert status==200 and len(body['items'])==1
        status,body=api('/api/v2/telemetry/segment/status',{'segment_id':item['segment_id'],
                    'active':False,'expected_revision':0,'reason':'bad','confirm':True})
        assert status==200 and body['active'] is False
        status,body=api('/api/v2/telemetry/series/search?role=test')
        assert status==200 and body['count']==0
        status,body=api('/api/v2/telemetry/governance/audit?limit=10')
        assert status==200 and all(row['actor']=='automated-contract-test' for row in body['items'])
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def test_v2_dataset_inventory_remains_visible_after_last_fragment_is_quarantined(tmp_path):
    from scripts.server.telemetry_v2_governance import dataset_summary
    path,_,item=store(tmp_path)
    summary=dataset_summary(path)
    assert summary['dataset_count']==1 and summary['items'][0]['channel_count']==1
    assert summary['items'][0]['segment_count']==1
    set_segment_status(path,{'segment_id':item['segment_id'],'active':False,
                             'expected_revision':0,'reason':'quarantine','confirm':True})
    summary=dataset_summary(path)
    assert summary['dataset_count']==0 and summary['quarantined_segments']==1
    assert summary['telemetry_segments_total']==1
    listing=list_segments(path,role='test')
    assert len(listing['items'])==1 and listing['items'][0]['active']==0
