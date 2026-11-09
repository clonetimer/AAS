"""Verified run-level targets -> V2 sample labels, model aliases and run events.

Never treat fault metadata as model features. A FAULT label applies to the
simulation run at/after onset_s, not to the health of every telemetry sensor.
Old database is never overwritten by this tool. No unknown real satellite
model names are invented: SIM-SAT-### are synthetic dataset selectors.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from scripts.target_archive_evidence import read_targets
import shutil
import sqlite3
import zipfile

SCHEMA = '''
CREATE TABLE v2_run_annotations(
 source_id INTEGER PRIMARY KEY REFERENCES v2_sources(id),
 token TEXT NOT NULL UNIQUE,
 model_alias TEXT NOT NULL UNIQUE,
 model_identity_kind TEXT NOT NULL CHECK(model_identity_kind='synthetic_alias'),
 split TEXT NOT NULL,
 group_id TEXT NOT NULL,
 run_role TEXT NOT NULL CHECK(run_role IN ('NORMAL','FAULT')),
 fault_type TEXT,
 fault_component TEXT,
 onset_s REAL,
 fault_end_exclusive_s REAL,
 normal_frames INTEGER NOT NULL,
 fault_frames INTEGER NOT NULL,
 annotation_scope TEXT NOT NULL CHECK(annotation_scope='simulation_run'),
 metadata_not_model_features INTEGER NOT NULL CHECK(metadata_not_model_features=1),
 target_sha256 TEXT NOT NULL,
 target_archive_sha256 TEXT NOT NULL,
 provenance TEXT NOT NULL
);
CREATE INDEX idx_v2_run_annotations_split ON v2_run_annotations(split,run_role);
'''

# Deliberate, explicit mapping of ALL 56 known telemetry fields to physical
# subsystem/part and variable. Does not infer an unmentioned sensor or component.
def identify(column):
    if column.startswith('eps.pdu.'):
        return ('eps','power_distribution_unit',column[len('eps.pdu.'):])
    if column == 'eps.battery_energy_j':
        return ('eps','battery','energy_j')
    if column == 'eps.bus_load_power_w':
        return ('eps','electric_power_bus','load_power_w')
    if column == 'eps.solar_array_power_w':
        return ('eps','solar_array','power_w')
    if column == 'adcs.pointing_error_deg':
        return ('adcs','attitude_control','pointing_error_deg')
    if column.startswith('adcs.sensor.gyro_'):
        return ('adcs','gyroscope',column[len('adcs.sensor.'):])
    if column.startswith('adcs.sensor.accel_'):
        return ('adcs','accelerometer',column[len('adcs.sensor.'):])
    for prefix,device in (
        ('adcs.star_tracker.','star_tracker'),('adcs.sun_sensor.','sun_sensor'),
        ('adcs.magnetometer.','magnetometer'),('adcs.rw.','reaction_wheel'),
        ('navigation.position_','orbit_state'),('navigation.velocity_','orbit_state'),
        ('payload.','payload_instrument'),('data.','data_storage'),
        ('comm.','communication_link'),('propulsion.','thruster_control'),
        ('thermal.battery_heater_','battery_heater'),
        ('thermal.','thermal_monitor'),
    ):
        if column.startswith(prefix):
            subsystem = column.split('.')[0]
            if prefix=='thermal.' and column.endswith('_temp_c'):
                component=column[len(prefix):-len('_temp_c')]
                return ('thermal',component+'_thermal_monitor','temp_c')
            return (subsystem,device,column[len(prefix):])
    raise ValueError(f'遥测字段未定义分系统/部件映射，拒绝猜测: {column}')


def unit(column, prior):
    if column.startswith('adcs.magnetometer.tesla_'):return 'T'
    if column.startswith('adcs.star_tracker.q'):return '1'
    if column.startswith('adcs.sun_sensor.direction_'):return '1'
    if column.startswith('navigation.position_m_'):return 'm'
    if column.startswith('navigation.velocity_m_s_'):return 'm/s'
    if column.startswith('adcs.sensor.gyro_measured_rad_s_') or column.startswith('adcs.rw.measured_speed_rad_s_'):return 'rad/s'
    if column.startswith('adcs.sensor.accel_measured_m_s2_'):return 'm/s²'
    if column.endswith(('.valid','.active','.enabled','.commanded','.permitted','.geometric_access',
                        '_valid','_active','_enabled','_commanded','_permitted','_geometric_access')):
        # Phase 53 delivery used dot-separated suffixes only; the delivered
        # telemetry columns use underscore suffixes (e.g. eps.pdu.adcs_commanded).
        # All such columns were verified to carry only 0/1 values in the 19
        # source files, so they are dimensionless flags.
        return '1'
    return prior


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as file:
        for block in iter(lambda:file.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def select_targets(zip_path:Path, tokens):
    with zipfile.ZipFile(zip_path) as archive:
        mapping={}
        for info in archive.infolist():
            if not info.filename.startswith('targets/') or not info.filename.endswith('.json'):continue
            token=Path(info.filename).stem
            if token not in tokens:continue
            if token in mapping:raise ValueError(f'标签 token 重复: {token}')
            raw=archive.read(info)
            target=json.loads(raw)
            if target.get('token')!=token or target.get('role') not in ('NORMAL','FAULT'):
                raise ValueError(f'标签无效: {token}')
            if target.get('metadata_not_model_features') is not True:
                raise ValueError(f'标签缺少防数据泄漏声明: {token}')
            if not isinstance(target.get('split'),str) or not target['split'] or not isinstance(target.get('group'),str) or not target['group']:
                raise ValueError(f'缺少 split / group: {token}')
            if target['role']=='NORMAL':
                if any(target.get(k) is not None for k in ('fault','onset_s','component')):
                    raise ValueError(f'正常样本带故障标注: {token}')
            else:
                onset=target.get('onset_s')
                if not isinstance(onset,(float,int)) or not math.isfinite(onset) or onset<0 or not target.get('fault') or not target.get('component'):
                    raise ValueError(f'故障标注不完整: {token}')
            mapping[token]=(target,hashlib.sha256(raw).hexdigest())
    missing=set(tokens)-set(mapping)
    if missing:raise ValueError(f'缺失上传原始数据对应的 targets: {sorted(missing)}')
    return mapping


def annotate_in_place(db_path:Path,targets_zip:Path):
    archive_sha=sha256(targets_zip)
    conn=sqlite3.connect(db_path,timeout=120)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=120000')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA synchronous=OFF')  # only on disposable output copy
        info=conn.execute('SELECT id,namespace,source_name,source_sha256,source_row_count FROM v2_sources ORDER BY source_name').fetchall()
        if not info or any(not x[2].endswith('.csv.gz') for x in info):
            raise ValueError('该库不是本次上传的 csv.gz 独立运行数据集')
        tokens={row[2][:-len('.csv.gz')] for row in info}
        if len(tokens)!=len(info):raise ValueError('来源 token 重复')
        # Validate the ENTIRE archive first, including group/split leakage,
        # even when this database imports only a selected subset of its runs.
        verified_archive=read_targets(targets_zip)
        missing=tokens-set(verified_archive)
        if missing:raise ValueError('缺失上传原始数据对应的 targets: '+str(sorted(missing)))
        mappings={token:(verified_archive[token]['target'],verified_archive[token]['target_sha256'])
                  for token in tokens}
        if conn.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=\'v2_run_annotations\'').fetchone():
            raise ValueError('该数据库已带 targets 标注，拒绝重复标注')
        if conn.execute("SELECT COUNT(*) FROM v2_samples WHERE label_int IS NOT NULL").fetchone()[0]:
            raise ValueError('原始数据库已含标签，拒绝静默覆盖')
        if conn.execute("SELECT DISTINCT model FROM v2_channels").fetchall()!=[('unspecified',)]:
            raise ValueError('原库卫星型号已设置，拒绝替换已声明真实身份')
        # Verify all selected run tokens before applying any source-level label changes.
        for row in info:
            token=row[2][:-len('.csv.gz')]
            registered=conn.execute('SELECT simulation_run_id FROM v2_upload_manifest WHERE source_id=?',(row[0],)).fetchone()
            if not registered or registered[0]!=token:raise ValueError(f'源文件缺少对应的运行契约: {token}')
        conn.executescript(SCHEMA)
        summaries=[]
        for idx,(source_id,ns,filename,source_sha,n_frames) in enumerate(info,1):
            token=filename[:-len('.csv.gz')]
            target,target_sha=mappings[token]
            alias=f'SIM-SAT-{idx:03d}'
            onset=target['onset_s']
            # Temporal label is a RUN-LEVEL ground truth, not proof that every
            # individual telemetry component itself has a localized failure.
            count_before=conn.execute('''SELECT COUNT(*) FROM v2_samples s JOIN v2_segments g ON s.segment_id=g.id
                         WHERE g.source_id=? AND s.time_sort_num<? AND g.kind='telemetry'
                         AND s.segment_id=(SELECT MIN(id) FROM v2_segments WHERE source_id=?)''',(source_id,onset if onset is not None else float('inf'),source_id)).fetchone()[0]
            if onset is not None and (count_before==0 or count_before>=n_frames):
                raise ValueError(f'故障发生时刻未处于样本范围内部: {token}')
            counts_fault=n_frames-count_before if onset is not None else 0
            run_end=conn.execute('SELECT MAX(time_sort_num) FROM v2_samples WHERE segment_id=(SELECT MIN(id) FROM v2_segments WHERE source_id=?)',(source_id,)).fetchone()[0]
            run_step=conn.execute('SELECT MIN(b.time_sort_num-a.time_sort_num) FROM v2_samples a JOIN v2_samples b ON b.segment_id=a.segment_id AND b.source_ordinal=a.source_ordinal+1 WHERE a.segment_id=(SELECT MIN(id) FROM v2_segments WHERE source_id=?)',(source_id,)).fetchone()[0]
            if run_step is None or run_step<=0:raise ValueError(f'未找到有效采样步长: {token}')
            fault_end=(run_end+run_step) if onset is not None else None
            channel_rows=conn.execute('''SELECT c.id,g.id,c.subsystem,c.device,c.variable,c.unit,g.source_table
                            FROM v2_channels c JOIN v2_segments g ON g.channel_id=c.id
                            WHERE g.source_id=? ORDER BY g.id''',(source_id,)).fetchall()
            if len(channel_rows)!=56 or any(r[1] is None for r in channel_rows):
                raise ValueError(f'通道数量或来源缺失: {token}')
            for channel_id,seg_id,sub,device,variable,old_unit,col in channel_rows:
                new_sub,new_device,new_variable=identify(col)
                conn.execute('''UPDATE v2_channels SET model=?,subsystem=?,device=?,variable=?,unit=? WHERE id=?''',
                             (alias,new_sub,new_device,new_variable,unit(col,old_unit),channel_id))
                if onset is None:
                    conn.execute('UPDATE v2_samples SET label_int=0,label_raw_json=? WHERE segment_id=?',
                                 ('"targets.zip:run-normal"',seg_id))
                else:
                    conn.execute('''UPDATE v2_samples SET label_int=CASE WHEN time_sort_num>=? THEN 1 ELSE 0 END,
                                  label_raw_json=? WHERE segment_id=?''',
                                 (onset,'"targets.zip:run-onset"',seg_id))
            conn.execute('''INSERT INTO v2_run_annotations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                source_id,token,alias,'synthetic_alias',target['split'],target['group'],target['role'],
                target['fault'],target['component'],onset,fault_end,count_before,counts_fault,
                'simulation_run',1,target_sha,archive_sha,
                'Exact token matched to targets.zip; binary sample state defined by time_s < onset_s versus >= onset_s; source_role simulation unchanged.'))
            conn.execute('''UPDATE v2_upload_manifest SET model_claim=?,labels_present=1,notes=? WHERE source_id=?''',
                 ('synthetic_alias:'+alias, 'Run-level targets.zip supervisory labels, separate split/group; metadata NOT model features; model is synthetic alias not physical satellite type.', source_id))
            conn.commit()
            print(f'[{idx}/{len(info)}] {alias} {token} {target["role"]} split={target["split"]} '
                  f'pre={count_before} fault={counts_fault} frames, channels={len(channel_rows)}',flush=True)
            summaries.append({'token':token,'model_alias':alias,'target_role':target['role'],
                 'split':target['split'],'group':target['group'],'fault':target['fault'],
                 'fault_component':target['component'],'onset_s':onset,
                 'normal_frames':count_before,'fault_frames':counts_fault,
                 'channels':len(channel_rows),'source_sha256':source_sha})
        from scripts.target_fault_location import install_fault_locations
        install_fault_locations(conn)
        check=conn.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=conn.execute('PRAGMA foreign_key_check').fetchall()
        if check!='ok' or foreign:raise RuntimeError(f'数据库损坏: {check}; FK: {foreign[:3]}')
        counts=dict(conn.execute('SELECT label_int,COUNT(*) FROM v2_samples GROUP BY label_int'))
        total=conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]
        if total!=sum(counts.values()) or set(counts)-{0,1} or len(summaries)!=len(info):
            raise RuntimeError('标注样本数量未能完全核对')
        conn.execute('INSERT INTO v2_meta(key,value) VALUES(?,?)',('target_annotation_sha256',archive_sha))
        conn.execute('INSERT INTO v2_meta(key,value) VALUES(?,?)',('run_label_semantics','0: time_s < onset_s / normal run; 1: fault run at/after onset_s'))
        conn.execute('INSERT INTO v2_meta(key,value) VALUES(?,?)',('model_identity_kind','synthetic_alias_per_csv_run'))
        conn.commit()
        return {'sources':summaries,'runs':len(info),'channels':sum(row['channels'] for row in summaries),
                'sample_labels':{str(k):v for k,v in counts.items()},'total_samples':total,
                'targets_total':None,'targets_zip_sha256':archive_sha,'integrity_check':check,
                'model_names_are_synthetic':True,'split_is_metadata_not_v2_train_role':True}
    finally:
        conn.close()


def build_copy(input_db,output_db,targets_zip):
    input_db=Path(input_db).resolve();output_db=Path(output_db).resolve()
    if not input_db.is_file():raise FileNotFoundError(input_db)
    if output_db.exists():raise FileExistsError(f'禁止覆盖现有数据库: {output_db}')
    output_db.parent.mkdir(parents=True,exist_ok=True)
    try:
        shutil.copy2(input_db,output_db)
        return annotate_in_place(output_db,Path(targets_zip).resolve())
    except BaseException:
        output_db.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description='创建带 targets 标注的独立 V2 新数据库（原库不变）')
    parser.add_argument('--source-db',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--targets',required=True,type=Path)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    summary=build_copy(args.source_db,args.output,args.targets)
    if args.report:args.report.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='sources'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
