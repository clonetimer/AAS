"""The browser selects multiple single-measurement tables, not columns."""
from pathlib import Path
import sqlite3
import unittest

from scripts.server.services import sqlite_rows

ROOT = Path(__file__).resolve().parents[1]

class BrowserMultiTableUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT/'web/visualization/browser.js').read_text(encoding='utf-8')
        cls.html = (ROOT/'web/visualization/browser.html').read_text(encoding='utf-8')
        cls.handler = (ROOT/'scripts/server/handlers.py').read_text(encoding='utf-8')

    def test_hierarchy_and_tables_use_checkbox_pickers(self):
        for name in ('modelFilter','subsystemFilter','deviceFilter','variableFilter','tableSelect'):
            self.assertIn(f'id="multi_{name}"', self.html)
        self.assertIn('refreshMultipleBrowserFilters',self.js)
        self.assertIn('state.selectedTables',self.js)
        self.assertNotIn('id="featureChips"',self.html)
        self.assertNotIn('id="trainFeatureList"', (ROOT/'web/visualization/runner.html').read_text('utf8'))

    def test_reference_y_axis_does_not_recalculate_series(self):
        self.assertIn('referenceTableSelect',self.js)
        self.assertIn('state.referenceTable=event.target.value;renderMultiTableChart()',self.js)
        self.assertIn('browserTimestamp(row.timestamp)',self.js)
        self.assertIn('d.max===d.min?0.5',self.js)

    def test_cache_headers_and_resource_refresh(self):
        self.assertIn('no-store, max-age=0',self.handler)
        self.assertIn('browser.js?unified=1',self.html)
        self.assertIn('styles-light.css?layout_compact=1', self.html)
        self.assertIn('browser.html?table_series=2', (ROOT/'web/visualization/workbench.js').read_text('utf8'))

    def test_demo_single_sensor_tables_have_value_and_labels(self):
        db=ROOT/'datasets/history_sample.db'
        with sqlite3.connect(db) as conn:
            columns={r[1] for r in conn.execute('PRAGMA table_info("DEMO_test_temperature")')}
        self.assertEqual(columns,{'timestamp','value','label'})
        from scripts.server.services import sqlite_tables
        info = next(item for item in sqlite_tables(db) if item['name'] == 'DEMO_test_temperature')
        self.assertEqual(info['hierarchy'][0]['variable'], 'temperature')
        self.assertTrue(info['is_single_measurement'])
        data=sqlite_rows(db,'DEMO_test_temperature',limit=10)
        self.assertEqual(len(data['rows']),10)
        self.assertTrue(all(isinstance(r['value'],(int,float)) for r in data['rows']))
