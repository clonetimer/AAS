"""Clean uploaded-data V2 build, native preprocessing and fault interval overlay API."""
from __future__ import annotations
import csv
import gzip
import sqlite3
from pathlib import Path
import tempfile
import unittest

from scripts.build_uploaded_v2 import build
from scripts.server.telemetry_v2_access import list_channels,query_charts
from scripts.server.telemetry_v2_governance import create_event,list_events
from scripts.server.telemetry_v2_preprocess import preprocess


class Phase7Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        for j in range(2):
            with gzip.open(root/(f'run_{j}.csv.gz'),'wt',newline='',encoding='utf8') as stream:
                out=csv.writer(stream)
                out.writerow(['time_s','adcs.rw.measured_speed_rad_s_0','thermal.battery_temp_c'])
                for i in range(5):out.writerow([i*2,i+j+3,(i+1)*10+j])
        self.db=root/'new.sqlite'
        self.report=build(root,self.db)
        self.channels=list_channels(self.db,role='simulation')['items']
        self.cid=self.channels[0]['id']

    def test_build_no_legacy_and_independent_runs(self):
        self.assertEqual(self.report['run_count'],2)
        self.assertEqual(self.report['samples'],20)
        self.assertEqual(len(self.channels),4)
        self.assertEqual(len(set(x['satellite_instance'] for x in self.channels)),2)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM v2_legacy_rows').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM v2_fault_events').fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int IS NOT NULL').fetchone()[0],0)

    def test_query_isolated_and_event(self):
        series=query_charts(self.db,role='simulation',channel_ids=[self.cid],limit=10)['series'][0]
        self.assertEqual(len(series['points']),5)
        self.assertEqual(series['time_reference'][1],'s')
        ev=create_event(self.db,{'channel_id':self.cid,'role':'simulation','start':'2','end':'6',
            'label':1,'fault_type':'operator_confirmed','note':'人工标注，仅测试','confirm':True})
        self.assertEqual(ev['status'],'created')
        listed=list_events(self.db,channel_id=self.cid,role='simulation')['items']
        self.assertEqual((listed[0]['start_raw'],listed[0]['end_raw']),('2','6'))
        self.assertEqual(list_events(self.db,channel_id=self.cid+2,role='simulation')['items'],[])

    def test_cross_run_relative_time_overlay_preserves_separate_series(self):
        # User-facing comparison allows same elapsed-time X axis for different
        # simulated satellites. The two provenance identities remain distinct.
        other=next(x['id'] for x in self.channels if x['satellite_instance'] !=
                   next(c['satellite_instance'] for c in self.channels if c['id']==self.cid))
        overlaid=query_charts(self.db,role='simulation',channel_ids=[self.cid,other],limit=10)
        self.assertEqual(len(overlaid['series']),2)
        self.assertEqual({x['channel']['id'] for x in overlaid['series']},{self.cid,other})
        self.assertEqual(list_channels(self.db,role='train')['count'],0)
        self.assertEqual(list_channels(self.db,role='test')['count'],0)

    def test_immutable_derived_moving_average(self):
        req={'channel_id':self.cid,'role':'simulation','method':'moving_average',
            'params':{'window':3},'start':'0','end':'8'}
        initial=query_charts(self.db,role='simulation',channel_ids=[self.cid],limit=10)['series'][0]['points']
        preview=preprocess(self.db,req)
        self.assertEqual(preview['status'],'preview')
        self.assertEqual(preview['output_count'],5)
        self.assertEqual(preview['preview'][1]['value'],(initial[0]['value']+initial[1]['value'])/2)
        created=preprocess(self.db,{**req,'confirm':True},commit=True,actor='test')
        self.assertEqual(created['status'],'created')
        derived=query_charts(self.db,role='processed',channel_ids=[created['output_channel_id']],limit=10)['series'][0]
        self.assertEqual(derived['points'][1]['value'],preview['preview'][1]['value'])
        self.assertEqual(query_charts(self.db,role='simulation',channel_ids=[self.cid],limit=10)['series'][0]['points'],initial)
        with self.assertRaisesRegex(ValueError,'已经生成'):
            preprocess(self.db,{**req,'confirm':True},commit=True,actor='test')

    def test_leakage_and_difference(self):
        with self.assertRaisesRegex(ValueError,'只允许从训练'):
            preprocess(self.db,{'channel_id':self.cid,'role':'simulation','method':'zscore'})
        difference=preprocess(self.db,{'channel_id':self.cid,'role':'simulation','method':'difference'})
        self.assertEqual(difference['output_count'],4)
        with self.assertRaisesRegex(ValueError,'min < max'):
            preprocess(self.db,{'channel_id':self.cid,'role':'simulation','method':'clip','params':{'min':4,'max':4}})

if __name__=='__main__': unittest.main()
