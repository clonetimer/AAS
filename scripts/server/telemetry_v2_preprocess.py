"""Immutable V2-native telemetry preprocessing.

Loads only an identified logical channel and exact source role/time scope. Produces
new derived channels and segments, with clock and source-lineage contracts.
Never fits a model on test samples, writes to raw samples or derives labels.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics

from scripts.server.telemetry_v2_access import open_database,_timestamp,_time_expr,_clock_contracts,_run_view,_policy,VALID_ROLES
from scripts.server.telemetry_v2_governance import active_clause
from scripts.server.v2_quality import (hampel_forward, quality_summary, preview_curve, is_valid, HAMPEL_WINDOW)

MAX_SAMPLES=100000
METHODS={'moving_average','median_filter','clip','difference','zscore','hampel_forward'}

def _canonical(o):
    return json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def _input(db,req):
    if not isinstance(req,dict):raise ValueError('请求必须为 JSON 对象')
    cid=req.get('channel_id');role=req.get('role');method=req.get('method')
    if type(cid) is not int or cid<=0:raise ValueError('必须指定有效 channel_id')
    if role not in VALID_ROLES or role=='unspecified':raise ValueError('只能处理已确认用途的数据')
    if method not in METHODS:raise ValueError('不支持此预处理方法')
    params=req.get('params') or {}
    if not isinstance(params,dict):raise ValueError('params 必须是对象')
    if method=='hampel_forward':
        if set(params)-{'window'}:raise ValueError('Hampel 仅允许设置窗口，forward=3 与质量标记为固定策略')
        win=params.get('window',HAMPEL_WINDOW)
        if type(win) is not int or not 3<=win<=101 or win%2!=1:raise ValueError('Hampel 窗口必须为 3～101 之间的奇数')
        params={'window':win,'outlier':'hampel','missing':'forward','max_consecutive_fill':3,'keep_quality_flags':True}
    elif method in ('moving_average','median_filter'):
        if set(params)-{'window'}:raise ValueError('不支持的窗口参数')
        win=params.get('window',5)
        if type(win) is not int or not 2<=win<=501:raise ValueError('window 需要在 2～501 之间')
        params={'window':win}
    elif method=='clip':
        if set(params)-{'min','max'}:raise ValueError('不支持的裁剪参数')
        lo=params.get('min');hi=params.get('max')
        if type(lo) not in (int,float) or type(hi) not in (int,float) or not math.isfinite(lo) or not math.isfinite(hi) or lo>=hi:
            raise ValueError('clip 需要有限的 min < max')
        params={'min':float(lo),'max':float(hi)}
    elif params:raise ValueError('此方法不接受参数')
    if method=='zscore' and role!='train':
        raise ValueError('zscore 的均值和标准差只允许从训练用途数据拟合，避免测试数据泄漏')
    with open_database(db) as conn:
        ch=conn.execute('SELECT * FROM v2_channels WHERE id=?',(cid,)).fetchone()
        if not ch:raise ValueError('参量不存在')
        # Same trustworthy train/test *view* as data browsing. The 19 labeled
        # simulations remain physical role=simulation, and eligibility derives
        # from their verified run annotations; never rewrite their source role.
        segments=conn.execute("""SELECT g.id,g.time_domain FROM v2_segments g
            WHERE g.channel_id=? AND g.kind='telemetry' AND g.usable_for_timeseries=1
            AND """+_run_view(conn,role)+_policy(role)+active_clause(conn,'g')+
            ' ORDER BY g.id',(cid,role)).fetchall()
        if not segments:
            raise ValueError('所选参量在当前“'+('训练' if role=='train' else '测试' if role=='test' else role)+
                '”视图下没有有效数据分片。请刷新参量列表并检查运行标签、数据用途或分片隔离状态')
        domains={r['time_domain'] for r in segments}
        if len(domains)!=1:raise ValueError('存在混合时间基准，拒绝预处理')
        domain=next(iter(domains))
        clock=_clock_contracts(conn,[r['id'] for r in segments],domain)
        field=_time_expr(domain)
        start=_timestamp(req.get('start'),domain)
        end=_timestamp(req.get('end'),domain)
        if start is not None and end is not None and start>end:raise ValueError('开始时间晚于结束时间')
        slots=','.join('?' for _ in segments)
        where=[f's.segment_id IN ({slots})','s.channel_id=?']
        args=[*(segment['id'] for segment in segments),cid]
        if start is not None:where.append(field+' >= ?');args.append(start)
        if end is not None:where.append(field+' <= ?');args.append(end)
        rows=[dict(r) for r in conn.execute(f'''SELECT s.timestamp_raw,s.timestamp_type,s.time_sort_text,s.time_sort_num,
            s.time_utc_us,s.value_num,s.label_int,s.segment_id,s.source_ordinal,{field} AS time_key
            FROM v2_samples s
            WHERE {' AND '.join(where)} ORDER BY {field},s.segment_id,s.source_ordinal LIMIT ?''',[*args,MAX_SAMPLES+1])]
    if len(rows)>MAX_SAMPLES:raise ValueError('预处理数据超过 100000 点，请缩小时间范围，按区间分批执行')
    if not rows:raise ValueError('当前时间窗没有采样；请核对日期范围，或清空开始和结束时间使用全部可用数据')
    seen=set()
    for row in rows:
        if not is_valid(row['value_num']):
            if method!='hampel_forward':
                raise ValueError('存在非数值或缺测样本；请使用 Hampel + forward 预处理并检查质量统计')
            row['value_num']=None
        key=row['time_key']
        if key in seen:raise ValueError('时间戳存在重复采样，必须先治理数据分片')
        seen.add(key)
    signatures=[(r['segment_id'],r['source_ordinal'],r['timestamp_raw'],r['value_num'],r['label_int']) for r in rows]
    source_sha=hashlib.sha256(_canonical(signatures).encode()).hexdigest()
    return dict(ch), rows,params,method,role,domain,clock,source_sha

def _transform(rows,method,params):
    if method=='hampel_forward':
        return hampel_forward(rows,window=params['window'])
    points=[];history=[]
    fitted={}
    # Respect missing-data gaps: no smoothing or differencing across a large
    # temporal discontinuity (more than 4 x median positive sample interval).
    steps=[float(rows[i]['time_key']-rows[i-1]['time_key']) for i in range(1,len(rows))
           if isinstance(rows[i]['time_key'],(int,float)) and
           isinstance(rows[i-1]['time_key'],(int,float)) and
           rows[i]['time_key']>rows[i-1]['time_key']]
    median_step=statistics.median(steps) if steps else None
    breaks={i for i in range(1,len(rows)) if median_step is not None and
            isinstance(rows[i]['time_key'],(int,float)) and
            rows[i]['time_key']-rows[i-1]['time_key']>4*median_step}
    if method=='zscore':
        vals=[r['value_num'] for r in rows]
        mean=statistics.fmean(vals)
        std=statistics.pstdev(vals)
        if std<=1e-15:raise ValueError('方差为零，无法标准化')
        fitted={'mean':mean,'std':std}
    for i,row in enumerate(rows):
        if i in breaks:history=[]
        v=row['value_num']
        if method=='moving_average':
            history.append(v);history=history[-params['window']:]
            res=statistics.fmean(history)
        elif method=='median_filter':
            history.append(v);history=history[-params['window']:]
            res=statistics.median(history)
        elif method=='clip':res=min(max(v,params['min']),params['max'])
        elif method=='difference':
            if i==0 or i in breaks:continue  # no fabricated derivative across missing-data gaps
            res=v-rows[i-1]['value_num']
        else:res=(v-fitted['mean'])/fitted['std']
        if not math.isfinite(res):raise ValueError('预处理输出非有限数值')
        points.append((row,res))
    if not points:raise ValueError('至少需要两个样本才能计算差分')
    fitted['gap_breaks']=len(breaks)
    qualities=[{'status':'observed','original':r['value_num'],'processed':v,
                'is_outlier':False,'was_missing':False,'was_filled':False,'is_valid':True,
                'source_segment_id':r['segment_id'],'source_ordinal':r['source_ordinal']}
               for r,v in points]
    return points,qualities,fitted

def preprocess(db:Path,request:dict,*,commit=False,actor='internal'):
    ch,rows,params,method,role,domain,clock,source_sha=_input(db,request)
    points,qualities,fitted=_transform(rows,method,params)
    quality=quality_summary(qualities)
    recipe={'input_channel_id':ch['id'],'input_role':role,'method':method,'params':params,
        'start':request.get('start'),'end':request.get('end'),'source_sha256':source_sha,
        'source_clock':clock,'fitted_on_role':role if method=='zscore' else None,'fitted':fitted,
        'quality_policy':'retain_per_sample_v1'}
    digest=hashlib.sha256(_canonical(recipe).encode()).hexdigest()
    result={'status':'preview','method':method,'input_channel_id':ch['id'],
      'output_role':role if role in ('train','test') else 'processed',
      'input_count':len(rows),'output_count':len(points),'recipe_sha256':digest,
      'first_timestamp':points[0][0]['timestamp_raw'],'last_timestamp':points[-1][0]['timestamp_raw'],
      'preview':[{'timestamp':r['timestamp_raw'],'value':v} for r,v in points[:8]],
      'quality':quality,'plot_series':preview_curve(points,qualities),
      'sample_check':{'valid_count':quality['valid_samples'],'invalid_count':quality['invalid_samples'],
                      'eligible_for_training':quality['valid_samples']>=8 and role=='train',
                      'eligible_for_evaluation':quality['valid_samples']>0 and role=='test'}}
    if not commit:return result
    if request.get('confirm') is not True:raise ValueError('写入派生分片需要 confirm=true')
    if not isinstance(actor,str) or not actor.strip():actor='unknown'
    db=Path(db).resolve()
    with sqlite3.connect(db,timeout=90) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS v2_preprocess_jobs (
          id INTEGER PRIMARY KEY, output_channel_id INTEGER NOT NULL REFERENCES v2_channels(id),
          output_segment_id INTEGER NOT NULL REFERENCES v2_segments(id),
          input_channel_id INTEGER NOT NULL REFERENCES v2_channels(id),
          recipe_sha256 TEXT NOT NULL UNIQUE, recipe_json TEXT NOT NULL,
          input_sample_count INTEGER NOT NULL, output_sample_count INTEGER NOT NULL,
          actor TEXT NOT NULL, created_utc TEXT NOT NULL)''')
        if conn.execute('SELECT 1 FROM v2_preprocess_jobs WHERE recipe_sha256=?',(digest,)).fetchone():
            raise ValueError('相同源数据与预处理参数已经生成过派生分片，禁止重复写入')
        # Re-check input snapshot after acquiring the write lock: provenance cannot race.
        _,verified,_,_,_,_,_,current_sha=_input(db,request)
        if current_sha!=source_sha or len(verified)!=len(rows):raise ValueError('源数据发生变化，请重新预览')
        out_namespace='processed_'+digest[:20]
        now=datetime.now(timezone.utc).isoformat()
        source_id=conn.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,
          source_bytes,imported_utc,source_table_count,source_row_count) VALUES (?,?,?,?,?,?,?)''',
          (out_namespace,'V2_preprocess_'+method,source_sha,0,now,1,len(points))).lastrowid
        cid=conn.execute('''INSERT INTO v2_channels(namespace,satellite_instance,model,subsystem,device,variable,unit)
          VALUES(?,?,?,?,?,?,?)''', (out_namespace,ch['satellite_instance'],ch['model'],ch['subsystem'],ch['device'],
          ch['variable']+'__'+method,ch['unit'])).lastrowid
        seg=conn.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,role_provenance,
           time_domain,usable_for_timeseries,schema_sql,columns_json,source_row_count,time_start_raw,time_end_raw)
           VALUES(?,?,?,'telemetry',?,'v2_preprocess_derived',?,1,NULL,'["timestamp","value","label"]',?,?,?)''',
           (source_id,'derived_'+method,cid,result['output_role'],domain,len(points),result['first_timestamp'],result['last_timestamp'])).lastrowid
        conn.execute('''CREATE TABLE IF NOT EXISTS v2_segment_clock(
         segment_id INTEGER PRIMARY KEY REFERENCES v2_segments(id),reference TEXT NOT NULL,
         unit TEXT NOT NULL,declared_by TEXT NOT NULL)''')
        if domain=='numeric_unspecified':
            conn.execute('INSERT INTO v2_segment_clock VALUES(?,?,?,?)',(seg,clock[0],clock[1],'V2 preprocess source clock'))
        conn.execute('''CREATE TABLE IF NOT EXISTS v2_preprocess_quality (
          output_segment_id INTEGER NOT NULL REFERENCES v2_segments(id),
          source_ordinal INTEGER NOT NULL, status TEXT NOT NULL,
          original_value REAL, processed_value REAL, is_outlier INTEGER NOT NULL,
          was_missing INTEGER NOT NULL, was_filled INTEGER NOT NULL, is_valid INTEGER NOT NULL,
          input_segment_id INTEGER NOT NULL, input_source_ordinal INTEGER NOT NULL,
          PRIMARY KEY(output_segment_id, source_ordinal))''')
        sql='''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,
          time_domain,time_sort_text,time_sort_num,time_utc_us,value_num,value_raw_json,label_int,
          label_raw_json,raw_row_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        conn.executemany(sql,[(seg,cid,i,r['timestamp_raw'],r['timestamp_type'],domain,
             r['time_sort_text'],r['time_sort_num'],r['time_utc_us'],v,_canonical(v),r['label_int'],
             _canonical(r['label_int']),_canonical({'quality':qualities[i]['status'],
                 'source_segment_id':r['segment_id'],'source_ordinal':r['source_ordinal']}))
             for i,(r,v) in enumerate(points)])
        conn.executemany('''INSERT INTO v2_preprocess_quality
          (output_segment_id,source_ordinal,status,original_value,processed_value,is_outlier,
           was_missing,was_filled,is_valid,input_segment_id,input_source_ordinal)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
          [(seg,i,q['status'],q['original'],q['processed'],int(q['is_outlier']),
            int(q['was_missing']),int(q['was_filled']),int(q['is_valid']),
            q['source_segment_id'],q['source_ordinal']) for i,q in enumerate(qualities)])
        conn.execute('''INSERT INTO v2_preprocess_jobs(output_channel_id,output_segment_id,input_channel_id,
           recipe_sha256,recipe_json,input_sample_count,output_sample_count,actor,created_utc)
           VALUES(?,?,?,?,?,?,?,?,?)''',(cid,seg,ch['id'],digest,_canonical(recipe),len(rows),len(points),actor[:120],now))
        conn.commit()
    result.update(status='created',output_channel_id=cid,output_segment_id=seg,namespace=out_namespace)
    return result


def list_quality_jobs(db: Path, *, limit=30):
    """Show past derived-series quality statistics without scanning source tables."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit 必须在 1～100 之间')
    with open_database(db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_preprocess_jobs'").fetchone():
            return {'items': [], 'count': 0}
        jobs=[dict(row) for row in conn.execute('''SELECT j.id,j.output_channel_id,j.output_segment_id,
          j.input_channel_id,j.input_sample_count,j.output_sample_count,j.created_utc,j.actor,
          c.variable,c.model,c.subsystem,c.device,j.recipe_json
          FROM v2_preprocess_jobs j JOIN v2_channels c ON c.id=j.output_channel_id
          ORDER BY j.id DESC LIMIT ?''', (limit,))]
        has_quality=bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2_preprocess_quality'").fetchone())
        for job in jobs:
            recipe=json.loads(job.pop('recipe_json'))
            job['method']=recipe['method']
            if has_quality:
                q=conn.execute('''SELECT status,COUNT(*) FROM v2_preprocess_quality
                      WHERE output_segment_id=? GROUP BY status''',(job['output_segment_id'],)).fetchall()
                counts={row[0]:row[1] for row in q}
                total=sum(counts.values())
                job['quality']=({'total':total,'valid_samples':sum(n for k,n in counts.items() if k=='observed' or k.startswith('filled_')),
                                'invalid_samples':sum(n for k,n in counts.items() if k.startswith('unfilled_')),
                                'by_status':counts} if total else None)
            else:
                job['quality']=None  # legacy derived job without quality metadata
        return {'items':jobs,'count':len(jobs)}
