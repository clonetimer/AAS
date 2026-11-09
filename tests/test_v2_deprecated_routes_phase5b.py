"""Live HTTP regression for the new single-V2 public contract."""
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
import json

from tests.http_test_handler import AuthorizedTestHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_old_public_table_and_simulation_routes_are_gone():
    server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
    worker=Thread(target=server.serve_forever,daemon=True)
    worker.start()
    opener=build_opener(NoRedirect())
    url=f'http://127.0.0.1:{server.server_port}'

    def fetch(path,post=None):
        body=json.dumps(post).encode('utf8') if post is not None else None
        req=Request(url+path,data=body,method='POST' if post is not None else 'GET',
                    headers={'Content-Type':'application/json'} if body else {})
        try:
            response=opener.open(req,timeout=5)
        except HTTPError as err:
            response=err
        with response:
            content=response.read()
            if response.headers.get('Content-Type','').startswith('application/json'):
                content=json.loads(content)
            return response.code,content,response.headers

    try:
        for path in ('/api/databases','/api/sqlite-tables','/api/simulation/tables',
                     '/api/simulation/status','/api/preprocessing/status',
                     '/api/backup/list'):
            code, payload,_=fetch(path)
            assert code==410,(path,code,payload)
        for path in ('/api/train-model','/api/evaluate-model','/api/run-evaluation',
                     '/api/simulation/init','/api/simulation/inject','/api/import-data',
                     '/api/run-experiment','/api/backup/restore', '/api/save-experiment'):
            code,payload,_=fetch(path,{})
            assert code==410,(path,code,payload)
        code,_,headers=fetch('/simulation.html')
        assert code==200  # V2 fault simulation is now a supported standalone page
        code,summary,_=fetch('/api/home/summary')
        assert code==200 and summary['databases'] in (0,1)
        if summary['databases']:
            assert summary['tables']>0
            code,catalog,_=fetch('/api/v2/telemetry/series/search?role=train')
            assert code==200 and 'items' in catalog
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
