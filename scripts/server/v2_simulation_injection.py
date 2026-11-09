"""V2 sparse fault injection: write ONLY selected injected telemetry into a new DB.

Unlike the retired simulation flow, this module never copies a source SQLite
file. The output is a V2 database containing sampled, injected channels and
an auditable source/operation manifest. Original CSV files stay unchanged.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from scripts.rebuild_telemetry_v2 import CANON_SCHEMA
from scripts.server.telemetry_v2_wide_import import import_wide_csv

SIM_NAME = 'aas_simulation_v2.sqlite'
FAULT_TYPES = ('bias', 'drift', 'stuck', 'spike')
MAX_POINTS = 4000
FAULT_COLUMNS = ('adcs.rw.measured_speed_rad_s_0', 'adcs.rw.measured_speed_rad_s_1', 'adcs.rw.measured_speed_rad_s_2')


def initialize(path: Path) -> None:
    """Create V2 schema in a NEW file only, never replace an existing database."""
    path = Path(path).resolve()
    if path.exists():
        with sqlite3.connect(path) as db:
            row = db.execute("SELECT value FROM v2_meta WHERE key='schema_version'").fetchone()
            if row != ('2',):
                raise ValueError('existing simulation database is not V2')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(CANON_SCHEMA)
        db.execute("INSERT INTO v2_meta(key,value) VALUES('schema_version','2')")
        db.commit()


def _manifest(db: sqlite3.Connection):
    db.execute('''CREATE TABLE IF NOT EXISTS v2_injection_records(
        id INTEGER PRIMARY KEY, source_token TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        source_path TEXT NOT NULL, source_column TEXT NOT NULL,
        injected_model TEXT NOT NULL, fault_type TEXT NOT NULL,
        onset_s REAL NOT NULL, magnitude REAL NOT NULL,
        sampled_rows INTEGER NOT NULL, injected_rows INTEGER NOT NULL,
        source_id INTEGER NOT NULL REFERENCES v2_sources(id),
        details_json TEXT NOT NULL)''')


def inject_from_gzip(root: Path, *, token: str, fault_type='bias', onset_s=120.0,
                     magnitude=12.0, max_points=400, column=FAULT_COLUMNS[0],
                     model=None, db_path: Path | None = None) -> dict[str, Any]:
    """Inject into a bounded *subset* of a trusted local CSV source.

    Only sampled selected columns are saved, not a full source database. This
    handles timewise point labels (0 before onset, 1 from onset onwards) and
    does not assert that unrelated channels themselves failed.
    """
    root = Path(root).resolve()
    if not isinstance(token, str) or len(token) != 24 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('source token must be a 24-character hex ID')
    if fault_type not in FAULT_TYPES or column not in FAULT_COLUMNS:
        raise ValueError('unsupported fault type or telemetry column')
    if (not isinstance(onset_s,(int,float)) or not math.isfinite(onset_s) or onset_s <= 0
            or not isinstance(magnitude,(int,float)) or not math.isfinite(magnitude) or magnitude == 0
            or type(max_points) is not int or not 8 <= max_points <= MAX_POINTS):
        raise ValueError('invalid onset, magnitude or sample bound')
    source = root / 'datasets' / 'source_csv_gz' / (token + '.csv.gz')
    if not source.is_file() or source.is_symlink():
        raise ValueError('source must be an installed immutable CSV.gz file')
    # A fault injection demo is a synthetic alteration of a verified healthy
    # reference. Never silently label an already faulty source as NORMAL.
    import zipfile
    targets=root/'datasets'/'source_labels'/'targets.zip'
    with zipfile.ZipFile(targets) as archive:
        name='targets/'+token+'.json'
        if name not in archive.namelist():
            raise ValueError('source file has no verified uploaded target')
        target_info=json.loads(archive.read(name))
        if target_info.get('token')!=token or target_info.get('role')!='NORMAL':
            raise ValueError('fault injection requires a verified NORMAL source run')

    target = Path(db_path).resolve() if db_path else root / 'datasets' / SIM_NAME
    # Disallow source DB overwrite: simulation is always a dedicated V2 file.
    if target.name != SIM_NAME or target.parent != (root/'datasets').resolve():
        raise ValueError('simulation database location must be datasets/'+SIM_NAME)
    model = model or ('INJECT-' + token[:8].upper())
    if not isinstance(model,str) or not model or len(model)>60 or any(ord(c)<32 for c in model):
        raise ValueError('invalid synthetic model ID')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    result_rows=[]
    with gzip.open(source,'rt',encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f)
        if 'time_s' not in (reader.fieldnames or []) or column not in (reader.fieldnames or []):
            raise ValueError('source CSV lacks required columns')
        for record in reader:
            t=float(record['time_s'])
            if not math.isfinite(t): raise ValueError('invalid source time')
            if len(result_rows)>=max_points: break
            # only selected telemetry is present in output
            values=[float(record[key]) for key in FAULT_COLUMNS]
            if not all(math.isfinite(x) for x in values):raise ValueError('invalid source sample')
            result_rows.append((t, values))
    if not result_rows or onset_s<=result_rows[0][0] or onset_s>result_rows[-1][0]:
        raise ValueError('fault onset must occur inside the selected sample interval')
    prev=None
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['time_s','label',*FAULT_COLUMNS])
    n_fault=0
    for i,(t,values) in enumerate(result_rows):
        if prev is not None and t<=prev:raise ValueError('source timestamps not increasing')
        prev=t
        is_fault=t>=onset_s
        if is_fault:
            n_fault+=1
            pos=FAULT_COLUMNS.index(column)
            if fault_type=='bias': values[pos]+=magnitude
            elif fault_type=='drift': values[pos]+=magnitude*(t-onset_s)/max(1.0,result_rows[-1][0]-onset_s)
            elif fault_type=='stuck': values[pos]=magnitude
            elif fault_type=='spike' and (i % 8 == 0): values[pos]+=magnitude
        writer.writerow([f'{t:.12g}',int(is_fault),*values])
    # Each distinct injection configuration is a separate run. Otherwise two
    # biases on the same source and onset could collide at identical timestamps.
    fingerprint=hashlib.sha256(json.dumps({
        'source_sha256':source_hash,'model':model,'column':column,'fault_type':fault_type,
        'onset_s':onset_s,'magnitude':magnitude,'max_points':len(result_rows)
    },sort_keys=True).encode()).hexdigest()[:20]
    payload={'namespace':'injected_'+token+'_'+fingerprint,
        'model':model,'satellite_instance':'','subsystem':'adcs','device':'reaction_wheel',
        'unit':'rad/s','role':'test','time_domain':'numeric_unspecified',
        'time_reference':'injected_run:'+token+':'+fingerprint,'time_unit':'s',
        'filename':token+'_injected.csv','csv':output.getvalue(),
        'column_metadata':{name:{'subsystem':'adcs','device':'reaction_wheel',
                        'variable':name[len('adcs.rw.'):],'unit':'rad/s'} for name in FAULT_COLUMNS}}
    initialize(target)
    # Validate and commit all three telemetry columns in one transaction.
    imported=import_wide_csv(target,payload,commit=True)
    result={'status':'success','database':SIM_NAME,'model':model,'source_token':token,
       'source_sha256':source_hash,'fault_type':fault_type,'fault_channel':column,
       'onset_s':onset_s,'magnitude':magnitude,'sampling_rows':len(result_rows),
       'fault_rows':n_fault,'source_id':imported['source_id'],
       'channels':imported['channels'], 'source_database_copied':False}
    with sqlite3.connect(target) as db:
        db.execute('PRAGMA foreign_keys=ON')
        _manifest(db)
        db.execute('''INSERT INTO v2_injection_records(source_token,source_sha256,source_path,
          source_column,injected_model,fault_type,onset_s,magnitude,sampled_rows,injected_rows,source_id,details_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
          (token,source_hash,'datasets/source_csv_gz/'+source.name,column,model,fault_type,
           onset_s,magnitude,len(result_rows),n_fault,imported['source_id'],json.dumps(result,ensure_ascii=False)))
        db.commit()
    return result


def list_injections(root:Path):
    path=Path(root)/'datasets'/SIM_NAME
    if not path.exists():return {'database':SIM_NAME,'items':[]}
    with sqlite3.connect(path) as db:
        _manifest(db)
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute(
            '''SELECT id,source_token,injected_model,fault_type,source_column,onset_s,
               magnitude,sampled_rows,injected_rows,details_json FROM v2_injection_records ORDER BY id DESC''')]
        for row in rows:
            try:details=json.loads(row.pop('details_json'))
            except (ValueError,TypeError):details={}
            row['start_frame']=details.get('start_frame',row['sampled_rows']-row['injected_rows']+1)
            row['end_frame']=details.get('end_frame',row['sampled_rows'])
        return {'database':SIM_NAME,'items':rows}



def preview_injection(root:Path, record_id:int, *,max_rows:int=1200):
    if type(record_id) is not int or record_id<=0:
        raise ValueError('invalid injection record ID')
    path=Path(root)/'datasets'/SIM_NAME
    if not path.is_file():raise ValueError('simulation database is absent')
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        record=db.execute('SELECT * FROM v2_injection_records WHERE id=?',(record_id,)).fetchone()
        if record is None:raise ValueError('injection record not found')
        seg=db.execute('SELECT id FROM v2_segments WHERE source_id=? AND source_table=?',
            (record['source_id'],record['source_column'])).fetchone()
        if seg is None:raise ValueError('saved injection has no telemetry segment')
        rows=db.execute('SELECT time_sort_num,value_num,label_int FROM v2_samples WHERE segment_id=? ORDER BY source_ordinal LIMIT ?',
                        (seg['id'],max_rows)).fetchall()
        details=json.loads(record['details_json'])
        return {'record':{'id':record_id,'fault_type':record['fault_type'],
               'onset_s':record['onset_s'],'magnitude':record['magnitude'],
               'start_frame':details.get('start_frame',record['sampled_rows']-record['injected_rows']+1),
               'end_frame':details.get('end_frame',record['sampled_rows']),
               'source_column':record['source_column'],'model':record['injected_model']},
                'points':[{'frame':i,'t':r['time_sort_num'],'value':r['value_num'],'label':r['label_int']} for i,r in enumerate(rows,1)]}


def export_injection_csv(root: Path, record_id: int, *, spreadsheet_safe: bool = True) -> bytes:
    """Download every saved sample in its original frame order (not a preview).

    A historical injection stores one selected channel, not a copy of the
    source satellite database. `label` represents *simulated* fault exposure.
    """
    if type(record_id) is not int or record_id <= 0:
        raise ValueError('无效故障模拟记录 ID')
    path = Path(root).resolve() / 'datasets' / SIM_NAME
    if not path.is_file():
        raise ValueError('没有可导出的模拟数据库')
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        record = db.execute('SELECT * FROM v2_injection_records WHERE id=?', (record_id,)).fetchone()
        if record is None:
            raise ValueError('模拟记录不存在或已删除')
        details = json.loads(record['details_json'])
        if not isinstance(details, dict):
            raise ValueError('模拟记录元数据损坏')
        samples = db.execute('''SELECT s.source_ordinal,s.time_sort_num,s.value_num,s.label_int
            FROM v2_samples s JOIN v2_segments g ON g.id=s.segment_id
            WHERE g.source_id=? AND g.source_table=?
            ORDER BY s.source_ordinal''',
            (record['source_id'], record['source_column'])).fetchall()
    if not samples or len(samples) != record['sampled_rows']:
        raise ValueError('模拟记录缺失采样，拒绝输出不完整数据表')
    csv_file = io.StringIO(newline='')
    writer = csv.writer(csv_file, lineterminator='\n')
    writer.writerow(('frame','time_s','value','label',
                     'source_record','source_model','source_column','fault_type'))

    def safe_cell(value):
        if spreadsheet_safe and isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
        return value

    for index, item in enumerate(samples, 1):
        writer.writerow(tuple(safe_cell(value) for value in (
            index, item['time_sort_num'], item['value_num'], item['label_int'],
            f'v2:{record_id}', details.get('source_model') or record['injected_model'],
            record['source_column'], record['fault_type'],
        )))
    return csv_file.getvalue().encode('utf-8')


def seed_default(root: Path):
    """Idempotent installed demonstration from an actually NORMAL uploaded run."""
    import zipfile
    src=Path(root)/'datasets'/'source_labels'/'targets.zip'
    with zipfile.ZipFile(src) as archive:
        targets=[]
        for path in sorted(archive.namelist()):
            if path.startswith('targets/') and path.endswith('.json'):
                obj=json.loads(archive.read(path))
                if (obj.get('role')=='NORMAL' and obj.get('split')=='train'
                        and (Path(root)/'datasets'/'source_csv_gz'/(obj['token']+'.csv.gz')).is_file()):
                    targets.append(obj['token'])
    if not targets:raise ValueError('cannot find a verified normal-run source for demo injection')
    token=targets[0]
    existing=list_injections(root)['items']
    if any(item['source_token']==token and item['injected_model']=='DEMO-RW-BIAS' for item in existing):
        return {'status':'exists','database':SIM_NAME,'source_token':token}
    return inject_from_gzip(root,token=token,onset_s=400,magnitude=18,max_points=600,model='DEMO-RW-BIAS')

# Phase 16: V2-native source selection / non-mutating preview / transactional save.
# Old CSV.gz injection is retained for the one-time demonstration seeder, but
# the formal UI no longer needs access to source_csv_gz.

def _source_rows(db: sqlite3.Connection, source_id: int, channel_id: int, count: int):
    # Same source-ordered sample window shared by catalog, bounds and preview.
    from scripts.server.telemetry_v2_governance import active_clause
    return db.execute('''SELECT q.time_domain,q.time_sort_num,q.time_utc_us,q.value_num,q.label_int
        FROM v2_samples q JOIN v2_segments g ON q.segment_id=g.id
        WHERE g.source_id=? AND g.channel_id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
        '''+active_clause(db,'g')+'''
        ORDER BY g.id,q.source_ordinal LIMIT ?''',
        (source_id,channel_id,count)).fetchall()


def _verified_normal_prefix(rows):
    # No unlabeled/faulty/invalid row can be reinterpreted as normal.
    values=[]
    domain=None
    prev=None
    offset=None
    for row in rows:
        current=row['time_domain']
        if current not in ('numeric_unspecified','datetime_utc') or (domain is not None and current!=domain):
            break
        stamp=(row['time_utc_us']/1e6 if current=='datetime_utc' and row['time_utc_us'] is not None
               else row['time_sort_num'] if current=='numeric_unspecified' else None)
        value=row['value_num']
        if (row['label_int']!=0 or stamp is None or value is None
            or not math.isfinite(float(stamp)) or not math.isfinite(float(value))
            or (prev is not None and stamp<=prev)):
            break
        if domain is None:
            domain=current
            offset=stamp if domain=='datetime_utc' else 0.0
        values.append((float(stamp-offset),float(value)))
        prev=stamp
    return values


def v2_source_catalog(root: Path) -> dict[str, Any]:
    # Train/test denote evaluation usage, NOT healthy/faulty state.
    # A fault-labeled run can supply its verified pre-fault normal prefix.
    from scripts.server.v2_database_config import active_path
    from scripts.server.telemetry_v2_access import open_database
    from scripts.server.telemetry_v2_governance import active_clause
    path = active_path(root)
    if path.name == SIM_NAME:
        raise ValueError('请将主 V2 遥测数据库设为活动数据库，而非模拟数据库')
    with open_database(path) as db:
        annotations=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_run_annotations'").fetchone()
        eligible="(g.role IN ('train','test') AND g.role_provenance NOT IN ('name_hint_only','database_hint_only'))"
        if annotations:
            # Annotated FAULT runs also contain a verified pre-fault normal prefix.
            eligible += " OR (g.role='simulation' AND EXISTS (SELECT 1 FROM v2_run_annotations a WHERE a.source_id=g.source_id AND a.run_role IN ('NORMAL','FAULT') AND a.metadata_not_model_features=1))"
        valid=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_segment_governance'").fetchone()
        governance=active_clause(db,'g') if valid else ''
        sql=('''SELECT DISTINCT g.source_id,c.id AS channel_id,c.model,c.subsystem,c.device,
              c.variable,c.unit,s.source_name,g.role AS source_role
              FROM v2_segments g JOIN v2_channels c ON c.id=g.channel_id
              JOIN v2_sources s ON s.id=g.source_id
              WHERE g.kind='telemetry' AND g.usable_for_timeseries=1 AND ('''
              +eligible+')'+governance+
              ' ORDER BY c.model,c.subsystem,c.device,c.variable,s.id')
        records=db.execute(sql).fetchall()
        run_roles=({r['source_id']:r['run_role'] for r in db.execute(
            'SELECT source_id,run_role FROM v2_run_annotations WHERE metadata_not_model_features=1')}
            if annotations else {})
        items=[]
        seen=set()
        for r in records:
            key=(r['source_id'],r['channel_id'])
            if key in seen:
                continue
            seen.add(key)
            # An eligible source needs at least eight consecutive clean,
            # explicitly label=0 samples; never include faulty/unlabeled rows.
            if len(_verified_normal_prefix(_source_rows(db,*key,8)))<8:
                continue
            items.append({**dict(r),'source_run_role':run_roles.get(r['source_id'],'UNSPECIFIED')})
        return {'database':path.name,'items':items,
                'selection_rule':'连续、有效且显式标注正常(label=0)的采样前缀；支持故障运行的故障前区间'}


# Legacy V2 types keep their existing absolute-value semantics. Additional
# fault types are served by the installed registry, without copying databases.
_V2_FAULT_TYPES = {
    'bias': '偏置', 'drift': '线性漂移', 'stuck': '固定值',
    'spike': '周期尖峰',
}


def v2_fault_type_catalog() -> list[dict[str, Any]]:
    """Return only runnable V2 types with their declarative parameter schemas."""
    from src.fault_plugins.registry import available_fault_types, discover_fault_types
    discover_fault_types()
    items = [{'name':name,'label':label,'description':'内置 V2 注入方式',
              'builtin':True,'config_schema':[], 'uses_severity':False,
              'v2_legacy':True} for name,label in _V2_FAULT_TYPES.items()]
    for item in available_fault_types():
        # V2 original names have different numerical semantics; do not shadow.
        if item['name'] in _V2_FAULT_TYPES:
            continue
        if not item['dependencies_status']['installed']:
            continue
        items.append({**item,'v2_legacy':False})
    return items


def _validate_plugin_parameters(kind: str, params: Any, magnitude: float) -> dict:
    """Validate installed plugin configuration before running untrusted inputs."""
    from src.fault_plugins.registry import normalize_fault_config
    types = {x['name']:x for x in v2_fault_type_catalog()}
    item = types.get(kind)
    if not item: raise ValueError('所选故障类型未安装或缺少依赖')
    if not isinstance(params,dict): raise ValueError('故障类型参数必须是对象')
    allowed = {str(x['name']) for x in item.get('config_schema',[]) if isinstance(x,dict) and x.get('name')}
    if set(params) - allowed: raise ValueError('故障类型参数包含未声明字段')
    if magnitude < 0: raise ValueError('插件故障强度必须非负')
    # Noise is deterministic for preview/save consistency. Existing plugins may
    # define seed in their explicit schema; otherwise do not allow one from UI.
    return normalize_fault_config(kind, {**params,'mode':'full','severity':magnitude})


def _apply_fault_type(kind: str, selected: list[dict], magnitude: float, params: dict) -> list:
    from src.fault_plugins.registry import create_fault_injector, validate_fault_output
    from copy import deepcopy
    normalized = _validate_plugin_parameters(kind, params, magnitude)
    if kind == 'noise':
        # The registry's noise injector accepts seed even though its schema is
        # empty. Use a stable seed without accepting arbitrary UI-supplied fields.
        normalized['seed'] = 0
    source=deepcopy(selected)
    transformed=create_fault_injector(kind).inject(deepcopy(source), normalized)
    validate_fault_output(source, transformed)
    result=[]
    for row in transformed:
        value=row['value']
        if value is not None:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError('故障类型插件返回了无效的数值')
            value=float(value)
        result.append(value)
    return result


def v2_injection_time_bounds(root: Path, *, source_id: int, channel_id: int, max_points: int) -> dict:
    from scripts.server.v2_database_config import active_path
    from scripts.server.telemetry_v2_access import open_database
    if not all(type(v) is int and v > 0 for v in (source_id,channel_id,max_points)) or not 8 <= max_points <= MAX_POINTS:
        raise ValueError('请选择有效的来源参量和采样点数')
    if not any(r['source_id']==source_id and r['channel_id']==channel_id for r in v2_source_catalog(root)['items']):
        raise ValueError('无法使用此来源进行正常遥测故障注入')
    with open_database(active_path(root)) as db:
        rows=_source_rows(db,source_id,channel_id,max_points)
    samples=_verified_normal_prefix(rows)
    if len(samples)<8:
        raise ValueError('所选来源不足 8 个连续、有效且显式标注正常的采样点')
    return {'source_id':source_id,'channel_id':channel_id,'max_points':max_points,
            'start_exclusive':samples[0][0],'end_inclusive':samples[-1][0],
            'sampling_rows':len(samples),'time_unit':'s',
            'baseline_truncated':len(samples)<len(rows),
            'selection_rule':'仅使用来源故障发生之前的连续正常采样'}

def _v2_injected_preview(root: Path, config: dict) -> dict[str, Any]:
    """Compute selected segment and fault samples without any database writes."""
    from scripts.server.v2_database_config import active_path
    from scripts.server.telemetry_v2_access import open_database
    allowed = {'source_id','channel_id','fault_type','onset_s','start_frame','end_frame','magnitude','max_points','plugin_params'}
    if not isinstance(config,dict) or set(config)-allowed:
        raise ValueError('故障模拟参数不合法')
    by_frame='start_frame' in config or 'end_frame' in config
    if by_frame and ('start_frame' not in config or 'end_frame' not in config or 'onset_s' in config):
        raise ValueError('请同时指定开始帧与结束帧，不要混用秒数')
    try:
        source_id=int(config['source_id']); channel_id=int(config['channel_id'])
        onset=None if by_frame else float(config['onset_s'])
        magnitude=float(config['magnitude']); count=int(config['max_points'])
        kind=config['fault_type']
        plugin_params=config.get('plugin_params',{})
    except (ValueError,TypeError,KeyError) as exc:
        raise ValueError('请选择来源参量并填写有效注入参数') from exc
    if (source_id<1 or channel_id<1 or kind not in {x['name'] for x in v2_fault_type_catalog()} or
        (not by_frame and (not math.isfinite(onset) or onset<=0)) or
        not math.isfinite(magnitude) or magnitude==0 or
        not 8<=count<=MAX_POINTS):
        raise ValueError('故障类型、时间、幅值或采样点数不合法')
    path=active_path(root)
    if path.name==SIM_NAME:raise ValueError('不能使用模拟数据库作为故障注入的原始来源')
    if not any(r['source_id']==source_id and r['channel_id']==channel_id
               for r in v2_source_catalog(root)['items']):
        raise ValueError('未找到可用的正常遥测来源参量')
    with open_database(path) as db:
        meta=db.execute('''SELECT c.model,c.subsystem,c.device,c.variable,c.unit,s.source_name,
            s.source_sha256 FROM v2_channels c JOIN v2_sources s ON s.id=? WHERE c.id=?''',
            (source_id,channel_id)).fetchone()
        if meta is None:
            raise ValueError('所选来源参量不存在')
        rows=_source_rows(db,source_id,channel_id,count)
    samples=_verified_normal_prefix(rows)
    if len(samples)<8:
        raise ValueError('所选来源不足 8 个连续有效的正常采样点，拒绝注入')
    if by_frame:
        start,end=config['start_frame'],config['end_frame']
        if (type(start) is not int or type(end) is not int or
                not 1<=start<=end<=len(samples)):
            raise ValueError(f'帧区间必须为 1～{len(samples)} 的整数闭区间')
        onset=samples[start-1][0]
    else:
        if not samples[0][0]<onset<=samples[-1][0]:
            raise ValueError('故障开始时刻必须落在所选采样范围内部')
        start=next(i for i, (t,_) in enumerate(samples,1) if t>=onset)
        end=len(samples)
    custom = kind not in FAULT_TYPES
    if custom:
        values=_apply_fault_type(kind, [{'timestamp':str(t),'value':v} for t,v in samples[start-1:end]], magnitude, plugin_params)
    else:
        if plugin_params: raise ValueError('内置故障类型不接受插件参数')
        values=[]
    plugin_index=0
    points=[]; fault_count=0
    for i,(t,v) in enumerate(samples):
        affected=start-1<=i<end
        after=v
        if affected:
            fault_count+=1
            if custom:
                after=values[plugin_index]
                plugin_index+=1
            elif kind=='bias': after+=magnitude
            elif kind=='drift': after+=magnitude*(t-onset)/max(1.0,samples[end-1][0]-onset)
            elif kind=='stuck':after=magnitude
            elif kind=='spike' and ((i-(start-1) if by_frame else i)%8==0):after+=magnitude
        points.append({'frame':i+1,'t':t,'original':v,'value':after,'label':int(affected)})
    signature=hashlib.sha256(json.dumps({
        'source_db':path.name,'source_id':source_id,'channel_id':channel_id,
        'kind':kind,'onset':onset,'start_frame':start,'end_frame':end,
        'magnitude':magnitude,'plugin_params':plugin_params,'points':points},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'status':'preview','source_database':path.name,'source_id':source_id,
        'channel_id':channel_id,'source_name':meta['source_name'],
        'source_sha256':meta['source_sha256'],'model':meta['model'],
        'subsystem':meta['subsystem'],'device':meta['device'],
        'variable':meta['variable'],'unit':meta['unit'] or '未知',
        'fault_type':kind,'onset_s':onset,'start_frame':start,'end_frame':end,
        'magnitude':magnitude, 'plugin_params':plugin_params,
        'sampling_rows':len(points),'fault_rows':fault_count,
        'baseline_truncated':len(samples)<len(rows),
        'preview_sha256':signature,'points':points}


def preview_v2_injection(root:Path, config:dict) -> dict:
    return _v2_injected_preview(root,config)


def save_v2_injection(root:Path, config:dict) -> dict:
    """Save only the simulated points, segments and manifest in ONE transaction."""
    from uuid import uuid4
    if not isinstance(config,dict):raise ValueError('故障模拟参数不合法')
    expected=config.get('preview_sha256')
    if not isinstance(expected,str) or len(expected)!=64:
        raise ValueError('请先预览，再保存当前故障模拟')
    result=_v2_injected_preview(root,{k:v for k,v in config.items() if k!='preview_sha256'})
    if result['preview_sha256']!=expected:
        raise ValueError('来源数据或注入配置已变化，请重新预览')
    target=Path(root)/'datasets'/SIM_NAME
    initialize(target)
    points=result['points']
    source_namespace='injected_'+uuid4().hex
    injected_model='INJECT-'+uuid4().hex[:8].upper()
    var=result['variable']
    with sqlite3.connect(target, timeout=30) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        _manifest(db)
        db.execute('''CREATE TABLE IF NOT EXISTS v2_segment_clock(
            segment_id INTEGER PRIMARY KEY REFERENCES v2_segments(id),
            reference TEXT NOT NULL,unit TEXT NOT NULL,declared_by TEXT NOT NULL)''')
        raw=json.dumps(points,ensure_ascii=False,separators=(',',':'))
        sid=db.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
            imported_utc,source_table_count,source_row_count) VALUES(?,?,?,? ,datetime('now'),1,?)''',
            (source_namespace, result['source_name'],result['preview_sha256'],len(raw.encode()),len(points))).lastrowid
        cid=db.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable,unit)
            VALUES(?,'',?,?,?,?,?)''',
            (source_namespace,injected_model,result['subsystem'],result['device'],var,result['unit'])).lastrowid
        seg=db.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
            time_domain,usable_for_timeseries,columns_json,source_row_count,time_start_raw,time_end_raw)
            VALUES(?,?,?,'telemetry','test','user_explicit','numeric_unspecified',1,?,?,?,?)''',
            (sid,var,cid,json.dumps(['time_s','label',var]),len(points),str(points[0]['t']),str(points[-1]['t']))).lastrowid
        db.execute('''INSERT INTO v2_segment_clock(segment_id,reference,unit,declared_by)
            VALUES(?,?,?,?)''',(seg,source_namespace,'s','injection'))
        db.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
            time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
            VALUES(?,?,?,?,'numeric','numeric_unspecified',NULL,?,NULL,?,?,?,?,?)''',
            ((seg,cid,i,str(p['t']),p['t'],p['value'],json.dumps(p['value']),p['label'],
              json.dumps(p['label']),json.dumps(p,ensure_ascii=False)) for i,p in enumerate(points)))
        info={k:v for k,v in result.items() if k!='points'}
        info.update(source_model=result['model'],model=injected_model,source_id=sid,channels=[{'channel_id':cid,'segment_id':seg}],database=SIM_NAME,source_database_copied=False)
        new_id=db.execute('''INSERT INTO v2_injection_records(source_token,source_sha256,source_path,
            source_column,injected_model,fault_type,onset_s,magnitude,sampled_rows,injected_rows,source_id,details_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f"v2:{result['source_id']}:{result['channel_id']}",result['preview_sha256'],
             result['source_database'],var,injected_model,result['fault_type'],result['onset_s'],
             result['magnitude'],len(points),result['fault_rows'],sid,
             json.dumps(info,ensure_ascii=False))).lastrowid
        db.commit()
    info.update(status='success',id=new_id)
    return info


def delete_injection(root:Path, record_id:int) -> dict:
    """Delete one saved simulation atomically, never touching the source V2 DB."""
    if type(record_id) is not int or record_id<=0:
        raise ValueError('无效故障模拟记录 ID')
    path=Path(root)/'datasets'/SIM_NAME
    if not path.is_file():raise ValueError('没有模拟数据库')
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT source_id FROM v2_injection_records WHERE id=?',(record_id,)).fetchone()
        if row is None:raise ValueError('故障模拟记录不存在或已删除')
        source_id=row[0]
        segs=[r[0] for r in db.execute('SELECT id FROM v2_segments WHERE source_id=?',(source_id,))]
        cids=[r[0] for r in db.execute('SELECT DISTINCT channel_id FROM v2_segments WHERE source_id=?',(source_id,))]
        # Remove only rows owned by the selected injection source, not shared channels.
        for seg in segs:
            for table in ('v2_samples','v2_legacy_rows','v2_issues','v2_segment_clock'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    db.execute(f'DELETE FROM {table} WHERE segment_id=?',(seg,))
        db.execute('DELETE FROM v2_segments WHERE source_id=?',(source_id,))
        for cid in cids:
            if cid is not None and not db.execute('SELECT 1 FROM v2_segments WHERE channel_id=? LIMIT 1',(cid,)).fetchone():
                db.execute('DELETE FROM v2_channels WHERE id=?',(cid,))
        db.execute('DELETE FROM v2_injection_records WHERE id=?',(record_id,))
        db.execute('DELETE FROM v2_sources WHERE id=?',(source_id,))
        db.commit()
    return {'status':'deleted','id':record_id,'deleted_source_id':source_id,'deleted_segments':len(segs)}
