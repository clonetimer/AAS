"""Functional and HTTP checks for CSV/ZIP export and evaluation-view controls."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import uuid
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tests.http_test_handler import AuthorizedTestHandler
from scripts import visualization_server
from scripts.server import services
from scripts.server.table_export import build_table_export, list_exportable_tables


class TableExportTests(unittest.TestCase):
    def setUp(self):
        self.db_name = f"aas_export_{uuid.uuid4().hex}.db"
        self.path = services.PROJECT_ROOT / 'datasets' / self.db_name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE source(timestamp TEXT, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER)')
            conn.executemany('INSERT INTO source VALUES (?,?,?,?,?,?,?)', [
                (f'2026-01-01 00:{i % 60:02d}:00', 'S', 'ADCS', 'Gyro', 'bias', float(i), i % 2)
                for i in range(1507)
            ])
            conn.execute('CREATE TABLE source_clean AS SELECT * FROM source')
            conn.execute('CREATE TABLE "故障_quoted""table" AS SELECT * FROM source WHERE label = 1')
            conn.execute('CREATE TABLE private_metadata(secret TEXT)')
            conn.execute('INSERT INTO private_metadata VALUES ("never-export")')

    def tearDown(self):
        for suffix in ('', '-wal', '-shm'):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def test_list_protects_internal_tables_and_includes_clean(self):
        tables = list_exportable_tables(self.path, simulation_db_path=services.SIMULATION_DB_PATH)
        names = {t['name'] for t in tables}
        self.assertEqual(names, {'source', 'source_clean', '故障_quoted"table'})
        self.assertEqual(next(t for t in tables if t['name'] == 'source_clean')['kind'], '清洁数据')

    def test_single_full_csv_has_all_rows_and_header(self):
        stream, mime, filename = build_table_export(self.path, ['source'], simulation_db_path=services.SIMULATION_DB_PATH)
        with stream:
            text = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
            rows = list(csv.reader(text))
        self.assertTrue(mime.startswith('text/csv'))
        self.assertEqual(filename, 'table_export.csv')
        self.assertEqual(rows[0], ['timestamp', 'model', 'subsystem', 'device', 'variable', 'value', 'label'])
        self.assertEqual(len(rows), 1508)
        self.assertEqual(rows[-1][-2:], ['1506.0', '0'])

    def test_multiple_zip_and_table_name_mapping(self):
        names = ['source', 'source_clean', '故障_quoted"table']
        stream, mime, filename = build_table_export(self.path, names, simulation_db_path=services.SIMULATION_DB_PATH)
        with stream, zipfile.ZipFile(stream) as archive:
            self.assertEqual(mime, 'application/zip')
            self.assertEqual(filename, 'tables_export.zip')
            self.assertEqual(len(archive.namelist()), 4)
            self.assertIn('故障_quoted"table', archive.read('table_names.txt').decode('utf-8-sig'))
            self.assertEqual(len(list(csv.reader(io.StringIO(archive.read('table_002.csv').decode('utf-8-sig'))))), 1508)

    def test_injection_and_limits_rejected(self):
        for names in ([], ['source','source'], ['private_metadata'], ['source; DROP TABLE source'], ['source'] * 41):
            with self.subTest(names=names[:3]), self.assertRaises(ValueError):
                build_table_export(self.path, names, simulation_db_path=services.SIMULATION_DB_PATH)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM source').fetchone()[0], 1507)

    def test_ui_includes_both_export_buttons_and_evaluation_view(self):
        frontend = Path(__file__).resolve().parents[1] / 'web' / 'visualization'
        for name in ('browser', 'simulation'):
            html = (frontend / f'{name}.html').read_text(encoding='utf-8')
            self.assertIn('id="exportTablesBtn"', html)
            self.assertIn('id="exportTablesModal"', html)
            self.assertIn('table-export.js', html)
        runner = (frontend / 'runner.html').read_text(encoding='utf-8')
        runner_js = (frontend / 'runner.js').read_text(encoding='utf-8')
        self.assertIn('name="evalDataView" value="clean"', runner)
        self.assertIn('data_view: state.evalDataView', runner_js)
        self.assertIn('/api/preprocessing/status?', runner_js)


class TableExportHttpTests(TableExportTests):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def test_http_listing_and_download_binary(self):
        db = f'datasets/{self.db_name}'
        with urlopen(self.base + '/api/export-tables/list?db=' + quote(db), timeout=20) as response:
            entries = json.load(response)['items']
        self.assertIn('source_clean', {entry['name'] for entry in entries})
        for names in (['source'], ['source', 'source_clean']):
            body = json.dumps({'db': db, 'tables': names}).encode()
            request = Request(self.base + '/api/export-tables', data=body, headers={'Content-Type':'application/json'}, method='POST')
            with urlopen(request, timeout=20) as response:
                self.assertIn('attachment;', response.headers.get('Content-Disposition', ''))
                payload = response.read()
            if len(names) == 1:
                self.assertEqual(len(list(csv.reader(io.StringIO(payload.decode('utf-8-sig'))))), 1508)
            else:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    self.assertIn('table_002.csv', archive.namelist())

    def test_http_rejects_traversal_and_metadata(self):
        for db, tables in (('../../etc/passwd', ['source']), (f'datasets/{self.db_name}', ['private_metadata'])):
            request = Request(self.base + '/api/export-tables', data=json.dumps({'db':db,'tables':tables}).encode(), headers={'Content-Type':'application/json'}, method='POST')
            with self.assertRaises(HTTPError) as ctx:
                urlopen(request, timeout=20)
            self.assertEqual(ctx.exception.code, 400)


if __name__ == '__main__':
    unittest.main()


class EvaluationCleanViewHttpTests(unittest.TestCase):
    """Checks that explicit clean selection follows the trained model contract."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), AuthorizedTestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def test_clean_model_requires_matching_view_and_valid_clean_table(self):
        from unittest.mock import MagicMock, patch

        class Detector:
            def score(self, values):
                return values

        data_contract = {
            'use_clean_data': True, 'auto_preprocess': True,
            'pipeline_config': {'normalization_method':'min_max'},
            'feature_columns':['value'],
            'model_input_scaling': {'enabled': True, 'method': 'min_max', 'parameters': {}},
        }
        configuration = {'config': {'data_contract': data_contract}}
        score_mock = MagicMock(return_value={'result': {'metrics': {'accuracy': 0.5}}})
        request_base = {'model_id': 1, 'dataset': 'datasets/history_sample.db',
                        'table': 'DEMO_multifeature_test'}
        target = visualization_server
        with (patch.object(target,'get_model', return_value={'algorithm': 'zscore', 'file_path':'models/test'}),
              patch.object(target,'resolve_managed_model_dir', return_value=Path('models/test')),
              patch.object(target,'load_model', return_value=Detector()),
              patch.object(target,'load_model_config', return_value=configuration),
              patch.object(target,'run_evaluation', score_mock),
              patch.object(target,'update_model_evaluation'),
              patch.object(target,'check_clean_table_status', return_value={'status':'valid','clean_table':'DEMO_multifeature_test_clean'})):
            def post(data):
                request = Request(self.base + '/api/evaluate-model', method='POST',
                                  data=json.dumps(data).encode(),
                                  headers={'Content-Type':'application/json'})
                return urlopen(request, timeout=20)
            with self.assertRaises(HTTPError) as wrong:
                post({**request_base, 'data_view': 'raw'})
            self.assertEqual(wrong.exception.code, 400)
            score_mock.assert_not_called()
            with post({**request_base, 'data_view': 'clean'}) as response:
                payload = json.load(response)
            self.assertEqual(payload['status'], 'success')
            self.assertEqual(payload['input_contract']['use_clean_data'], True)
            self.assertFalse(score_mock.call_args.kwargs['auto_preprocess'])

            with patch.object(target,'check_clean_table_status',return_value={'status':'invalidated'}):
                with self.assertRaises(HTTPError) as stale:
                    post({**request_base, 'data_view': 'clean'})
            self.assertEqual(stale.exception.code, 400)
            self.assertEqual(score_mock.call_count, 1)

    def test_clean_table_status_handles_existing_quoted_identifiers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'quoted.db'
            with sqlite3.connect(db) as conn:
                for name in ('quote"example', 'quote"example_clean'):
                    conn.execute('CREATE TABLE "' + name.replace('"','""') + '" (timestamp TEXT, value REAL)')
                    conn.execute('INSERT INTO "' + name.replace('"','""') + '" VALUES (?, ?)', ('2026-01-01',1.5))
            actual = visualization_server.check_clean_table_status(db, 'quote"example')
            self.assertEqual(actual['status'], 'valid')
