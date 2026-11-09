"""A real HTTP check of protected alignment preview and hierarchy metadata."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

from tests.http_test_handler import AuthorizedTestHandler


class TableFusionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),AuthorizedTestHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(timeout=5);cls.server.server_close()

    def get(self,url):
        try:
            with urlopen(self.base+url,timeout=25) as resp:
                return resp.status,json.load(resp)
        except HTTPError as exc:
            return exc.code,json.load(exc)

    def test_table_catalog_provides_hierarchy_records(self):
        code,payload=self.get('/api/sqlite-tables?db=datasets/history_sample.db')
        self.assertEqual(code,200)
        item=next(item for item in payload['items'] if item['name']=='DEMO_train_temperature')
        self.assertEqual(item['hierarchy'][0]['variable'],'temperature')

    def test_alignment_preview_with_real_database(self):
        params=[('db','datasets/history_sample.db'),
                ('table','DEMO_train_temperature'),('table','DEMO_train_vibration')]
        code,payload=self.get('/api/train-alignment-preview?'+urlencode(params))
        self.assertEqual(code,200,payload)
        self.assertEqual(payload['aligned_count'],480)
        self.assertEqual(payload['coverage']['DEMO_train_temperature'],1.0)

    def test_logical_multi_preview_uses_channel_identity_instead_of_tables(self):
        params=[('db','datasets/history_sample.db'),
                ('table','DEMO_train_temperature'),('table','DEMO_train_vibration'),
                ('logical_multi','true')]
        code,payload=self.get('/api/train-alignment-preview?'+urlencode(params))
        self.assertEqual(code,200,payload)
        self.assertEqual(payload['fusion_kind'],'concatenate_each_channel_then_align')
        self.assertEqual(len(payload['logical_sources']),2)
        self.assertEqual(payload['feature_columns'],['value','source_2'])

    def test_alignment_preview_exposes_virtual_wide_samples_and_policy(self):
        params=[('db','datasets/history_sample.db'),
                ('table','DEMO_train_temperature'),
                ('table','DEMO_train_vibration'),
                ('tolerance','5'), ('method','previous')]
        code,payload=self.get('/api/train-alignment-preview?'+urlencode(params))
        self.assertEqual(code,200,payload)
        self.assertEqual(payload['alignment_method'],'previous')
        self.assertEqual(payload['alignment_policy'],'previous_with_tolerance')
        self.assertLessEqual(len(payload['preview_rows']),8)
        self.assertGreater(len(payload['preview_rows']),0)
        self.assertEqual(set(payload['preview_rows'][0]),{'timestamp','value','source_2','label','source_timestamp_1','source_timestamp_2','source_label_1','source_label_2'})

    def test_uniform_time_grid_preview_over_http(self):
        params=[('db','datasets/history_sample.db'),
                ('table','DEMO_train_temperature'),('table','DEMO_train_vibration'),
                ('timeline','fixed'),('interval','5'),
                ('tolerance','10'),('method','previous'),('label_method','previous')]
        code, payload=self.get('/api/train-alignment-preview?'+urlencode(params))
        self.assertEqual(code,200,payload)
        self.assertEqual(payload['timeline_mode'],'fixed')
        self.assertEqual(payload['grid_interval_seconds'],5)
        self.assertEqual(payload['label_mapping_policy'],'causal_previous_label')
        self.assertEqual(payload['grid_count'],959)
        self.assertEqual(payload['aligned_count'],959)
        self.assertTrue(payload['preview_rows'])
        first=payload['preview_rows'][0]
        self.assertIn('source_timestamp_1',first)
        self.assertIn('source_timestamp_2',first)
        self.assertEqual(first['label'],0)

    def test_uniform_grid_bad_params_rejected_over_http(self):
        params=[('db','datasets/history_sample.db'),
                ('table','DEMO_train_temperature'),('table','DEMO_train_vibration'),
                ('timeline','fixed'),('interval','0'),('tolerance','5')]
        code,payload=self.get('/api/train-alignment-preview?'+urlencode(params))
        self.assertEqual(code,400)
        self.assertIn('采样间隔',payload['error'])

    def test_training_preview_rejects_test_sets_and_duplicate_sources(self):
        for tables in [('DEMO_test_temperature','DEMO_test_vibration'),
                       ('DEMO_train_temperature','DEMO_train_temperature')]:
            params=[('db','datasets/history_sample.db')]+[('table',x) for x in tables]
            code,payload=self.get('/api/train-alignment-preview?'+urlencode(params))
            self.assertEqual(code,400)
            self.assertIn('error',payload)
