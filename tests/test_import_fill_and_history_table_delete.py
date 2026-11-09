"""End-to-end regression checks for optional imputation and history table delete."""
from __future__ import annotations

import json
import sqlite3
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.http_test_handler import AuthorizedTestHandler
from scripts import visualization_server
from scripts.server import services


COLUMNS = ('timestamp', 'model', 'subsystem', 'device', 'variable', 'value', 'label')
MAPPING = {name: {'type': 'column', 'value': name} for name in COLUMNS}
LONG_CSV = '''timestamp,model,subsystem,device,variable,value,label
0,S,EPS,Battery,soc,,0
1,S,EPS,Battery,soc,10,0
2,S,EPS,Battery,soc,100,0
3,S,EPS,Battery,soc,,1
4,S,EPS,Battery,soc,NA,1
5,S,EPS,Battery,soc,NaN,1
6,S,EPS,Battery,soc,,0
7,S,EPS,Battery,soc,1.5,0
8,S,EPS,Battery,soc,,0
'''


class FillAndDeleteTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"aas_fill_delete_{uuid.uuid4().hex}.db"
        self.path = services.PROJECT_ROOT / 'datasets' / self.db_name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(self.path).close()

    def tearDown(self):
        for suffix in ('', '-wal', '-shm'):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def test_fill_is_opt_in_grouped_causal_and_does_not_change_labels(self):
        result = services.import_data(
            LONG_CSV, 'csv', self.db_name, '', 'measurements', MAPPING,
            data_role='test', fill_missing=True,
        )
        self.assertEqual((result['missing_values'], result['filled_values'], result['unfilled_values']), (6, 5, 1))
        with sqlite3.connect(self.path) as conn:
            data = conn.execute('SELECT value,label FROM measurements ORDER BY CAST(timestamp AS INTEGER)').fetchall()
        self.assertEqual([item[0] for item in data], [None, 10, 100, 100, 100, 100, 100, 1.5, 1.5])
        self.assertEqual([item[1] for item in data], [0, 0, 0, 1, 1, 1, 0, 0, 0])

        raw = services.import_data(
            LONG_CSV, 'csv', self.db_name, '', 'raw', MAPPING,
            data_role='test', fill_missing=False,
        )
        self.assertEqual(raw['filled_values'], 0)
        with sqlite3.connect(self.path) as conn:
            data = [r[0] for r in conn.execute('SELECT value FROM raw ORDER BY CAST(timestamp AS INTEGER)')]
        self.assertEqual(data[3], '')
        self.assertEqual(data[4], 'NA')
        self.assertEqual(data[5], 'NaN')

    def test_wide_batch_keeps_series_independent_and_atomic(self):
        csv = 'timestamp,model,label,eps.battery.soc,eps.battery.voltage\n0,S,0,0.9,\n1,S,0,,12\n2,S,1,0.7,\n3,S,1,,\n'
        jobs = []
        for suffix, column in (('soc', 'eps.battery.soc'), ('voltage', 'eps.battery.voltage')):
            mapping = {
                'timestamp': {'type': 'column', 'value': 'timestamp'},
                'model': {'type': 'column', 'value': 'model'},
                'subsystem': {'type': 'manual', 'value': 'eps'},
                'device': {'type': 'manual', 'value': 'battery'},
                'variable': {'type': 'manual', 'value': suffix},
                'value': {'type': 'column', 'value': column},
                'label': {'type': 'column', 'value': 'label'},
            }
            jobs.append({'table_prefix': '', 'table_name': suffix, 'field_mappings': mapping, 'data_role': 'test'})
        response = services.import_data_multi(csv, 'csv', self.db_name, jobs, fill_missing=True)
        self.assertEqual(response['tables_created'], 2)
        self.assertEqual(response['filled_values_total'], 4)
        with sqlite3.connect(self.path) as conn:
            soc = [r[0] for r in conn.execute('SELECT value FROM soc ORDER BY timestamp')]
            volt = [r[0] for r in conn.execute('SELECT value FROM voltage ORDER BY timestamp')]
        self.assertEqual(soc, [0.9, 0.9, 0.7, 0.7])
        self.assertEqual(volt, [None, 12, 12, 12])

        broken_jobs = jobs + [{'table_name': 'bad/name', 'table_prefix': '', 'field_mappings': jobs[0]['field_mappings']}]
        with self.assertRaises(ValueError):
            services.import_data_multi(csv, 'csv', self.db_name, broken_jobs, fill_missing=True)
        with sqlite3.connect(self.path) as conn:
            self.assertNotIn('bad/name', {r[0] for r in conn.execute("SELECT name FROM sqlite_master")})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM soc').fetchone()[0], 4)

    def test_delete_selected_table_clean_companion_metadata_only(self):
        services.import_data(LONG_CSV, 'csv', self.db_name, '', 'target', MAPPING)
        services.import_data(LONG_CSV, 'csv', self.db_name, '', 'keep', MAPPING)
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE target_clean(value REAL)')
            conn.execute("INSERT INTO aas_table_meta(table_name, declared_role, role_source, source_file, imported_at) VALUES ('target_clean','auto','auto','','now')")
            conn.execute('CREATE TABLE internal_cache (value TEXT)')
        result = services.delete_history_table(self.path, 'target')
        self.assertEqual(result, {'status': 'success', 'table_name': 'target', 'deleted_clean_table': True})
        with sqlite3.connect(self.path) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            role_names = {r[0] for r in conn.execute('SELECT table_name FROM aas_table_meta')}
        self.assertNotIn('target', tables)
        self.assertNotIn('target_clean', tables)
        self.assertNotIn('target', role_names)
        self.assertNotIn('target_clean', role_names)
        self.assertIn('keep', tables)
        self.assertIn('keep', role_names)
        self.assertIn('internal_cache', tables)
        self.assertNotIn('target', {x['name'] for x in services.sqlite_tables(self.path)})

    def test_internal_and_missing_tables_are_protected(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE internal_cache(value TEXT)')
            conn.execute('CREATE TABLE foo_clean(model TEXT,subsystem TEXT,device TEXT,variable TEXT)')
            conn.execute('CREATE TABLE "quotes""test"(model TEXT,subsystem TEXT,device TEXT,variable TEXT)')
        for name in ('internal_cache', 'sqlite_master', 'foo_clean', 'absent', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                services.delete_history_table(self.path, name)
        # Quoted identifiers are safe when users have existing unusual table names.
        services.delete_history_table(self.path, 'quotes"test')
        with sqlite3.connect(self.path) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', ('quotes"test',)).fetchone())
            self.assertIsNotNone(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', ('internal_cache',)).fetchone())

    def test_json_nonfinite_and_mixed_series_are_rejected(self):
        json_rows = [
            {"timestamp": "0", "model": "S", "subsystem": "EPS", "device": "B", "variable": "soc", "value": 4, "label": 0},
            {"timestamp": "1", "model": "S", "subsystem": "EPS", "device": "B", "variable": "soc", "value": float('nan'), "label": 0},
            {"timestamp": "2", "model": "T", "subsystem": "EPS", "device": "B", "variable": "soc", "value": None, "label": 0},
        ]
        with self.assertRaisesRegex(ValueError, '单参量表'):
            services.import_data(json.dumps(json_rows), 'json', self.db_name, '',
                                 'json_values', MAPPING, fill_missing=True)
        with sqlite3.connect(self.path) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='json_values'").fetchone())

    def test_input_rejects_untyped_boolean(self):
        with self.assertRaisesRegex(ValueError, 'boolean'):
            services.import_data(LONG_CSV, 'csv', self.db_name, '', 'bad', MAPPING, fill_missing='false')


class FillAndDeleteHttpTests(FillAndDeleteTests):
    """Keep these tests isolated from the packaged demo databases."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=5)
        cls.server.server_close()

    def _post_json(self, url, payload):
        req = Request(self.base + url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def _post_multipart(self, url, values):
        boundary = 'AASBoundary' + uuid.uuid4().hex
        body = bytearray()
        for name, value in values.items():
            body.extend(f'--{boundary}\r\n'.encode())
            if name == 'file':
                body.extend(b'Content-Disposition: form-data; name="file"; filename="example.csv"\r\n')
                body.extend(b'Content-Type: text/csv\r\n\r\n')
            else:
                body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode())
            body.extend(b'\r\n')
        body.extend(f'--{boundary}--\r\n'.encode())
        req = Request(self.base + url, data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
        with urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def test_http_single_and_multi_and_delete(self):
        single = self._post_multipart('/api/import-data', {
            'file': LONG_CSV, 'target_db': 'datasets/' + self.db_name,
            'table_prefix': '', 'table_name': 'http_single',
            'data_role': 'test', 'field_mappings': json.dumps(MAPPING),
            'fill_missing': 'true',
        })
        self.assertEqual(single['filled_values'], 5)
        multi = self._post_multipart('/api/import-data-multi', {
            'file': LONG_CSV, 'target_db': 'datasets/' + self.db_name,
            'jobs': json.dumps([{'table_prefix': '', 'table_name': 'http_multi', 'field_mappings': MAPPING, 'data_role': 'test'}]),
            'fill_missing': 'false',
        })
        self.assertEqual(multi['filled_values_total'], 0)
        result = self._post_json('/api/sqlite-table/delete', {'db': 'datasets/' + self.db_name, 'table': 'http_single'})
        self.assertEqual(result['status'], 'success')
        with sqlite3.connect(self.path) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='http_single'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='http_multi'").fetchone())

    def test_http_rejects_traversal_and_internal_table(self):
        for payload in (
            {'db': '../../models/model.db', 'table': 'models'},
            {'db': 'datasets/' + self.db_name, 'table': 'sqlite_master'},
            {'db': 'datasets/' + self.db_name, 'table': 'bad_missing'},
        ):
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as error:
                self._post_json('/api/sqlite-table/delete', payload)
            self.assertEqual(error.exception.code, 400)


class FillAndDeleteUiContractTests(unittest.TestCase):
    def test_controls_api_wiring_and_destructive_confirmation(self):
        root = Path(__file__).resolve().parents[1] / 'web' / 'visualization'
        html = (root / 'browser.html').read_text(encoding='utf-8')
        js = (root / 'browser.js').read_text(encoding='utf-8')
        for fragment in ('id="fillMissingInput"', 'id="deleteTableBtn"', 'id="deleteTableModal"', 'id="deleteTableConfirmInput"'):
            self.assertIn(fragment, html)
        self.assertIn("formData.append('fill_missing', String(importEls.fillMissingInput.checked))", js)
        self.assertIn("/api/sqlite-table/delete", js)
        self.assertIn('els.deleteTableConfirmInput.value !== target.table', js)
        self.assertIn('syncDeleteTableButton()', js)


if __name__ == '__main__':
    unittest.main()
