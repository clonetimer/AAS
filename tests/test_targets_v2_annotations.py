"""Target integrity and scientific identity boundary (no large DB required)."""
import csv
import gzip
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
import zipfile
import json
from scripts.annotate_uploaded_targets_v2 import identify, select_targets, unit
from scripts.target_fault_location import FAULT_LOCATIONS

ROOT=Path(__file__).resolve().parents[1]
INPUTS=ROOT/'datasets'/'source_csv_gz'
TARGETS=ROOT/'datasets'/'source_labels'/'targets.zip'

class TargetLabelsTest(unittest.TestCase):
    def test_all_19_targets_match_sources(self):
        files=sorted(INPUTS.glob('*.csv.gz'))
        if not files:
            self.skipTest('精简源码包不附 source_csv_gz，原始文件匹配测试需外部数据目录')
        self.assertEqual(len(files),19)
        tokens={p.name[:-len('.csv.gz')] for p in files}
        targets=select_targets(TARGETS,tokens)
        self.assertEqual(set(targets),tokens)
        self.assertEqual(sum(v[0]['role']=='FAULT' for v in targets.values()),14)
        self.assertEqual(sum(v[0]['role']=='NORMAL' for v in targets.values()),5)
        for item,_ in targets.values():
            self.assertIs(item['metadata_not_model_features'],True)

    def test_every_field_has_unique_physical_mapping(self):
        files=sorted(INPUTS.glob('*.csv.gz'))
        if not files:
            self.skipTest('精简源码包不附 source_csv_gz，原始列审查需外部数据目录')
        for path in files:
            with gzip.open(path,'rt',encoding='utf-8-sig') as fh:
                fields=next(csv.reader(fh))
            self.assertEqual(fields[0],'time_s')
            self.assertEqual(len(fields),57)
            channels=[identify(col) for col in fields[1:]]
            self.assertEqual(len(set(channels)),56)
        self.assertEqual(identify('eps.pdu.payload_enabled')[0:2],('eps','power_distribution_unit'))
        self.assertEqual(identify('adcs.sensor.gyro_measured_rad_s_x')[0:2],('adcs','gyroscope'))
        self.assertEqual(identify('thermal.battery_heater_power_w')[0:2],('thermal','battery_heater'))
        self.assertEqual(unit('navigation.position_m_x',None),'m')

    def test_fault_source_locations_covered(self):
        names={p.name[:-len('.csv.gz')] for p in INPUTS.glob('*.csv.gz')}
        if not names:
            self.skipTest('精简源码包不附 source_csv_gz，故障源映射验证需外部数据目录')
        t=select_targets(TARGETS,names)
        self.assertEqual(set(v[0]['component'] for v in t.values() if v[0]['component']) - FAULT_LOCATIONS.keys(),set())
        self.assertEqual(FAULT_LOCATIONS['pdu.payload'][:2],('eps','pdu_payload_branch'))

    def test_target_mismatch_rejected(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'missing.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('targets/x.json',json.dumps({'token':'x','role':'NORMAL','fault':None,'component':None,
                         'onset_s':None,'split':'train','group':'foo','metadata_not_model_features':True}))
            with self.assertRaisesRegex(ValueError,'缺失'):
                select_targets(p,{'x','not-present'})

    def test_fault_onset_missing_rejected(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'invalid.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('targets/x.json',json.dumps({'token':'x','role':'FAULT','fault':'test','component':'gyro',
                         'onset_s':None,'split':'train','group':'foo','metadata_not_model_features':True}))
            with self.assertRaisesRegex(ValueError,'不完整'):
                select_targets(p,{'x'})

if __name__=='__main__':unittest.main()
