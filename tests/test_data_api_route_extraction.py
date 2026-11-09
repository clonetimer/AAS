"""HTTP contract regression tests for extracted read/write database routes."""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tests.http_test_handler import AuthorizedTestHandler
from scripts import visualization_server
from scripts.server import services


class DatabaseApiRouteContractTests(unittest.TestCase):
    """Keep the wire-level response semantics across the route extraction."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AuthorizedTestHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.name = f"aas_route_phase3_{uuid.uuid4().hex}.db"
        self.db = services.PROJECT_ROOT / "datasets" / self.name
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE dataset_meta (key TEXT, value TEXT)")
            conn.execute("INSERT INTO dataset_meta VALUES ('origin', 'route-regression')")
            conn.execute("CREATE TABLE device_table_index (device TEXT, table_name TEXT)")
            conn.execute("INSERT INTO device_table_index VALUES ('gyro', 'sample')")
            conn.execute("""CREATE TABLE sample (
                timestamp TEXT, model TEXT, subsystem TEXT, device TEXT,
                variable TEXT, value REAL, label INTEGER)""")
            conn.executemany("INSERT INTO sample VALUES (?,?,?,?,?,?,?)", [
                (f"2026-01-0{i + 1}", "SAT", "ADCS", "gyro", "bias", float(i), i % 2)
                for i in range(4)
            ])
            conn.execute("CREATE TABLE sample_clean AS SELECT * FROM sample")

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db) + suffix).unlink(missing_ok=True)

    def get_json(self, route, **params):
        """Perform a real HTTP GET, returning status and parsed JSON."""
        url = self.base + route
        if params:
            url += "?" + urlencode(params)
        try:
            with urlopen(url, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def post_json(self, route, data):
        """Perform a real HTTP POST, returning status and parsed JSON."""
        request = Request(self.base + route, method="POST", data=json.dumps(data).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_logical_telemetry_endpoint_finds_multiple_time_partitions(self):
        from scripts.server import series_catalog as catalog
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE sample_next (timestamp TEXT, value REAL, label INTEGER)')
            conn.execute("INSERT INTO sample_next VALUES ('2026-01-05', 11.0, 0)")
            catalog.register(conn, 'sample_next', {
                'model': 'SAT', 'subsystem': 'ADCS', 'device': 'gyro', 'variable': 'bias'
            }, 'compact')
        target = dict(db=f'datasets/{self.name}', model='SAT', subsystem='ADCS',
                      device='gyro', variable='bias', start_time='2026-01-03', end_time='2026-01-05')
        status, result = self.get_json('/api/telemetry-series', **target)
        self.assertEqual(status, 200)
        self.assertEqual([row['value'] for row in result['rows']], [2.0, 3.0, 11.0])
        self.assertEqual([row['source_table'] for row in result['rows']],
                         ['sample', 'sample', 'sample_next'])
        self.assertEqual(len(result['sources']), 2)
        status, invalid = self.get_json('/api/telemetry-series', db=target['db'], model='SAT')
        self.assertEqual(status, 400)
        self.assertIn('error', invalid)

    def test_logical_export_and_explicit_partition_refresh_http(self):
        import io
        import zipfile
        from urllib.request import Request, urlopen
        db = f'datasets/{self.name}'
        identity = {'model':'SAT','subsystem':'ADCS','device':'gyro','variable':'bias'}
        bad, response = self.post_json('/api/telemetry-partitions/refresh',
            {'db':db})
        self.assertEqual(bad, 400)
        self.assertIn('confirm', response['error'])
        code, result = self.post_json('/api/telemetry-partitions/refresh',
            {'db':db,'confirm':True})
        self.assertEqual(code, 200)
        self.assertEqual(result['indexed_tables'], 1)
        body = json.dumps({'db':db,'identity':identity,
            'start_time':'2026-01-02', 'end_time':'2026-01-03'}).encode()
        req = Request(self.base + '/api/export-logical-series', data=body,method='POST',
                      headers={'Content-Type':'application/json'})
        with urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), 'application/zip')
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                csv_text = archive.read('telemetry.csv').decode('utf-8-sig')
                self.assertIn('2026-01-02', csv_text)
                self.assertNotIn('2026-01-04', csv_text)
                info = json.loads(archive.read('manifest.json'))
                self.assertEqual(info['row_count'], 2)

    def test_read_routes_keep_response_shapes(self):
        db = f"datasets/{self.name}"
        status, result = self.get_json("/api/sqlite-tables", db=db)
        self.assertEqual(status, 200)
        self.assertIn("sample", {item["name"] for item in result["items"]})
        self.assertNotIn("sample_clean", {item["name"] for item in result["items"]})

        status, result = self.get_json("/api/sqlite-rows", db=db, table="sample", limit=2,
                                       offset=1, time_col="timestamp", start_time="2026-01-02")
        self.assertEqual(status, 200)
        self.assertEqual(result["total_count"], 4)
        self.assertEqual(result["matched_count"], 3)
        self.assertEqual([row["value"] for row in result["rows"]], [2.0, 3.0])

        status, result = self.get_json("/api/sqlite-time-range", db=db, table="sample",
                                       time_col="timestamp")
        self.assertEqual(status, 200)
        self.assertEqual((result["min"], result["max"]), ("2026-01-01", "2026-01-04"))

        status, result = self.get_json("/api/sqlite-meta", db=db)
        self.assertEqual((status, result["meta"]["origin"]), (200, "route-regression"))
        status, result = self.get_json("/api/device-index", db=db)
        self.assertEqual(status, 200)
        self.assertEqual(result["rows"][0]["device"], "gyro")
        status, result = self.get_json("/api/hierarchy-options", db=db, model="SAT")
        self.assertEqual(status, 200)
        self.assertIn("ADCS", result["subsystems"])

        status, result = self.get_json("/api/preprocessing/status", db=db, table="sample")
        self.assertEqual(status, 200)
        self.assertEqual(result["clean_table"], "sample_clean")
        status, result = self.get_json("/api/export-tables/list", db=db)
        self.assertEqual(status, 200)
        self.assertIn("sample_clean", {item["name"] for item in result["items"]})

    def test_missing_and_invalid_parameters_keep_json_errors(self):
        for path, params in (
            ("/api/sqlite-tables", {}),
            ("/api/sqlite-rows", {"db": "datasets/ignored.db"}),
            ("/api/sqlite-time-range", {"db": "datasets/ignored.db", "table": "sample"}),
            ("/api/preprocessing/status", {"db": "datasets/ignored.db"}),
            ("/api/export-tables/list", {}),
            ("/api/sqlite-meta", {}),
            ("/api/device-index", {}),
            ("/api/hierarchy-options", {}),
        ):
            with self.subTest(path=path):
                status, result = self.get_json(path, **params)
                self.assertEqual(status, 400)
                self.assertIn("error", result)
        status, result = self.get_json("/api/sqlite-rows", db=f"datasets/{self.name}",
                                       table="sample", limit=-1)
        self.assertEqual(status, 400)
        self.assertIn("non-negative", result["error"])

    def test_path_boundary_and_unknown_routes(self):
        for route, params, code in (
            ("/api/sqlite-rows", {"db": "../../etc/passwd", "table": "sample"}, 400),
            ("/api/import-data", {}, 404),
            ("/api/route-does-not-exist", {}, 404),
        ):
            with self.subTest(route=route):
                status, result = self.get_json(route, **params)
                self.assertEqual(status, code)
                self.assertIn("error", result)

    def test_delete_route_is_scoped_and_preserves_other_tables(self):
        db = f"datasets/{self.name}"
        status, result = self.post_json("/api/sqlite-table/delete",
                                        {"db": db, "table": "sample"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "success")
        with sqlite3.connect(self.db) as conn:
            remaining = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertNotIn("sample", remaining)
        self.assertNotIn("sample_clean", remaining)
        self.assertIn("dataset_meta", remaining)
        status, result = self.post_json("/api/sqlite-table/delete",
                                        {"db": db, "table": "dataset_meta"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_concurrent_reads_and_invalid_import_content(self):
        db = f"datasets/{self.name}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(
                lambda _: self.get_json("/api/sqlite-rows", db=db, table="sample", limit=4),
                range(10),
            ))
        self.assertTrue(all(code == 200 and len(data["rows"]) == 4
                            for code, data in replies))
        for route in ("/api/preview-import", "/api/import-data", "/api/import-data-multi"):
            with self.subTest(route=route):
                status, result = self.post_json(route, {})
                self.assertEqual(status, 400)
                self.assertIn("error", result)
