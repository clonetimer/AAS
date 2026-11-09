"""Real HTTP CRUD and event-based alignment on isolated managed SQLite only."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from tests.http_test_handler import AuthorizedTestHandler


class TestFaultIntervalHttp:
    """HTTP integration tests; pytest initializes resources per test."""
    path: Path
    db: str
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str
    def setup_method(self):
        from scripts.visualization_server import ROOT
        self.path = Path(ROOT) / 'datasets' / f'_test_events_{uuid4().hex}.db'
        with sqlite3.connect(self.path) as conn:
            for name in ('temp', 'vibration'):
                conn.execute(f'''CREATE TABLE "{name}" (timestamp REAL, model TEXT,
                    subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)''')
                conn.executemany(f'INSERT INTO "{name}" VALUES (?,?,?,?,?,?,?)', [
                    (float(i), 'S', 'ADCS', 'gyro', name, float(i), 0) for i in range(10)])
        self.db = self.path.name
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def teardown_method(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.path.unlink(missing_ok=True)

    def request(self, endpoint, data=None):
        if data is not None:
            req = Request(self.base + endpoint, data=json.dumps(data).encode(),
                          headers={'Content-Type':'application/json'}, method='POST')
        else:
            req = self.base + endpoint
        try:
            with urlopen(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_crud_and_preview_respects_unknown_and_boundaries(self):
        db = self.db
        for table, start, end, label in [('temp',0,4,0),('temp',4,7,1),('vibration',0,7,0)]:
            status, event = self.request('/api/fault-intervals', dict(db=db, table=table, start=start,end=end,label=label))
            assert status == 200, event
        status, payload = self.request('/api/fault-intervals?' + urlencode({'db':db,'table':'temp'}))
        assert status == 200 and len(payload['items']) == 2
        params = [('db',db),('table','temp'),('table','vibration'),('label_method','interval')]
        # This endpoint only accepts tables classified as normal training sources.
        status, preview = self.request('/api/train-alignment-preview?' + urlencode(params))
        assert status == 200, preview
        assert preview['aligned_count'] == 7
        assert preview['unknown_label_count'] == 3
        assert preview['preview_rows'][4]['label'] == 1
        event = payload['items'][1]
        status, _ = self.request('/api/fault-intervals', dict(db=db,table='temp',action='delete',id=event['id']))
        assert status == 200
        status, _ = self.request('/api/fault-intervals', dict(db=db,table='temp',action='delete',id=event['id']))
        assert status == 400

    def test_update_preserves_identity_and_rejects_overlap(self):
        db = self.db
        code, saved = self.request('/api/fault-intervals',
            dict(db=db, table='temp', start=1, end=3, label=1, fault_type='bias'))
        assert code == 200, saved
        event_id = saved['event']['id']
        code, updated = self.request('/api/fault-intervals',
            dict(db=db, table='temp', action='update', id=event_id,
                 start=2, end=4, label=1, fault_type='drift'))
        assert code == 200 and updated['event']['id'] == event_id, updated
        code, items = self.request('/api/fault-intervals?' + urlencode({'db':db,'table':'temp'}))
        assert code == 200 and items['items'][0]['start_timestamp'] == '2'
        assert items['items'][0]['fault_type'] == 'drift'
        code, _ = self.request('/api/fault-intervals',
            dict(db=db, table='temp', start=5, end=7, label=0))
        assert code == 200
        code, rejected = self.request('/api/fault-intervals',
            dict(db=db, table='temp', action='update', id=event_id,
                 start=3, end=6, label=1, fault_type='drift'))
        assert code == 400 and '重叠' in rejected['error']
        code, items = self.request('/api/fault-intervals?' + urlencode({'db':db,'table':'temp'}))
        assert code == 200 and items['items'][0]['start_timestamp'] == '2'

    def test_overlap_and_path_security(self):
        status, _ = self.request('/api/fault-intervals', dict(db=self.db,table='temp',start=2,end=4,label=1))
        assert status == 200
        status, body = self.request('/api/fault-intervals', dict(db=self.db,table='temp',start=3,end=5,label=0))
        assert status == 400 and '重叠' in body['error']
        status, _ = self.request('/api/fault-intervals', dict(db='../private.db',table='temp',start=0,end=1,label=1))
        assert status == 400

    def test_delete_table_cleans_events(self):
        from scripts.server.services import delete_history_table
        status, created = self.request('/api/fault-intervals', dict(db=self.db,table='temp',start=0,end=2,label=1))
        assert status == 200, created
        result = delete_history_table(self.path, 'temp')
        assert result['status'] == 'success'
        with sqlite3.connect(self.path) as db:
            assert not db.execute('SELECT 1 FROM aas_fault_intervals WHERE table_name=?', ('temp',)).fetchone()
