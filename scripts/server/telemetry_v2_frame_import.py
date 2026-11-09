"""Frame-coordinate wide CSV import. No time is inferred from row position."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scripts.server.import_cancellation import SourceImportCancelled
from scripts.server.telemetry_v2_access import open_database
from scripts.server.telemetry_v2_import import _config, _ensure_schema, _required, MAX_IMPORT_ROWS
from scripts.server.telemetry_v2_import_context import parse_context, check_conflicts, record_context


def import_frame_csv_stream(db_path, payload, stream, *, delimiter=',', commit=False,
                            source_digest, source_size, progress=None,
                            cancel_requested=None, before_commit=None):
    if delimiter not in (',', ';', '\t'):
        raise ValueError('CSV 分隔符无效')
    common = _config({**payload, 'coordinate_mode': 'frame', 'variable': '_external_wide_csv_',
                      'unit': payload.get('unit') or 'unknown'}, allow_external_filename=True)
    reader = csv.DictReader(stream, delimiter=delimiter, strict=True)
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)) or any(not name or name.startswith('__') for name in fields):
        raise ValueError('CSV 表头非法或重复')
    frame_col = payload.get('frame_column')
    if frame_col is None:
        frame_col = 'frame' if 'frame' in fields else None
    if frame_col is not None and frame_col not in fields:
        raise ValueError('指定的帧列不存在')
    if frame_col is None and payload.get('row_order_confirmed') is not True:
        raise ValueError('无显式帧列时必须确认原始行序代表帧序')
    label_col = payload.get('label_column')
    if label_col is None:
        label_col = next((name for name in ('label', 'fault_label') if name in fields), None)
    label_col = label_col or None
    if label_col is not None and label_col not in fields:
        raise ValueError('指定的标签列不存在')
    if frame_col == label_col and frame_col is not None:
        raise ValueError('帧列和标签列不能相同')
    if not label_col and not (common['role'] == 'train' and payload.get('all_normal_confirmed') is True
                              or common['role'] == 'test' and payload.get('unlabeled_test_confirmed') is True):
        raise ValueError('无标签数据须明确确认训练集全正常或测试集真值未知')
    selected = payload.get('selected_columns')
    candidates = [name for name in fields if name not in (frame_col, label_col)]
    if selected is not None:
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or set(selected)-set(candidates):
            raise ValueError('选择参量必须来自 CSV 测量列')
        columns = [name for name in candidates if name in selected]
    else:
        columns = candidates
    if not 1 <= len(columns) <= 64:
        raise ValueError('帧 CSV 须包含 1～64 个测量参量')
    mapping = payload.get('column_metadata') or {}
    if not isinstance(mapping, dict) or set(mapping)-set(columns):
        raise ValueError('参量映射无效')
    if payload.get('require_explicit_mapping') is True and set(mapping) != set(columns):
        raise ValueError('参量映射不完整')
    configs = {}
    for name in columns:
        meta = mapping.get(name) or {}
        if not isinstance(meta, dict) or set(meta)-{'subsystem','device','variable','unit'}:
            raise ValueError(f'{name} 参量映射非法')
        tokens = name.split('.')
        configs[name] = {**common,
            'subsystem': _required(meta.get('subsystem', tokens[0].upper() if len(tokens)>=3 else common['subsystem']), '分系统'),
            'device': _required(meta.get('device', '.'.join(tokens[1:-1]) if len(tokens)>=3 else common['device']), '部件'),
            'variable': _required(meta.get('variable', tokens[-1]), '参量'),
            'unit': _required(meta.get('unit', common['unit']), '单位', max_length=32)}
    if len({(cfg['subsystem'],cfg['device'],cfg['variable']) for cfg in configs.values()}) != len(columns):
        raise ValueError('多个 CSV 列映射到同一个参量')
    sequence = _required(payload.get('frame_sequence_id') or uuid4().hex, '帧序列 ID', max_length=160)
    rows = []
    missing = Counter()
    labels = set()
    previous = 0
    gaps = 0
    for index, record in enumerate(reader):
        if index >= MAX_IMPORT_ROWS:
            raise ValueError('单次最多导入 50000 帧')
        if cancel_requested and index % 100 == 0 and cancel_requested():
            raise SourceImportCancelled('用户取消 CSV 导入')
        if None in record or any(value is None or '\x00' in value for value in record.values()):
            raise ValueError(f'CSV 第 {index+2} 行结构错误')
        if frame_col:
            frame_text = record[frame_col].strip()
            if not frame_text.isascii() or not frame_text.isdecimal() or frame_text.startswith('0'):
                raise ValueError(f'CSV 第 {index+2} 行帧号必须是从 1 开始的十进制整数')
            frame = int(frame_text)
            if frame < 1 or frame > 2**53 or frame <= previous:
                raise ValueError(f'CSV 第 {index+2} 行帧号重复或乱序')
            gaps += frame - previous - 1
        else:
            frame = index + 1
        previous = frame
        raw_label = record[label_col].strip() if label_col else ('0' if common['role']=='train' else '')
        if raw_label not in ('0','1','') or (common['role']=='train' and raw_label!='0'):
            raise ValueError(f'CSV 第 {index+2} 行标签非法或训练集包含故障')
        label = int(raw_label) if raw_label else None
        if common['role']=='test' and label_col and label is None:
            raise ValueError('测试集不能混合已知和未知真值')
        labels.add(label)
        values=[]
        for name in columns:
            cell=record[name].strip()
            if cell.lower() in ('','na','n/a','null','nan'):
                values.append(None); missing[name]+=1
            else:
                try: value=float(cell)
                except (ValueError,OverflowError) as exc:
                    raise ValueError(f'CSV 第 {index+2} 行参量 {name} 不是数值') from exc
                if not math.isfinite(value):
                    raise ValueError(f'CSV 第 {index+2} 行参量 {name} 非有限数值')
                values.append(value)
        rows.append((index,frame,label,values,json.dumps(record,ensure_ascii=False,sort_keys=True)))
    if not rows:
        raise ValueError('CSV 不含数据行')
    if labels == {None} and common['role']!='test':
        raise ValueError('未知真值仅允许测试用途')
    if None in labels and len(labels)>1:
        raise ValueError('同一 CSV 不能混合未知与已知真值')
    quality = {'coordinate_mode':'frame','row_count':len(rows),'first_frame':rows[0][1],
               'last_frame':rows[-1][1],'missing_frames':gaps,
               'missing_by_column':dict(missing),'label_counts':{str(x):sum(r[2]==x for r in rows) for x in labels}}
    context = parse_context(payload, filename=common['filename'], labels=labels, role=common['role'])
    result = {'status':'imported' if commit else 'preview','coordinate_mode':'frame',
              'frame_sequence_id':sequence,'filename':common['filename'],'role':common['role'],
              'row_count':len(rows),'column_count':len(columns),'quality':quality,
              'missing_by_column':dict(missing),'missing_frames':gaps,
              'label_status':'provided' if label_col else 'confirmed_normal' if common['role']=='train' else 'unknown',
              'sha256':source_digest,'columns':[{'source_column':name,**{k:configs[name][k] for k in ('subsystem','device','variable','unit')}} for name in columns]}
    path=Path(db_path).resolve()
    with open_database(path): pass
    with sqlite3.connect(str(path),timeout=30) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE' if commit else 'BEGIN')
        try:
            _ensure_schema(conn)
            check_conflicts(conn,context,namespace=common['namespace'],model=common['model'])
            identities={}
            for name in columns:
                cfg=configs[name]
                identity=tuple(cfg[k] for k in ('namespace','satellite_instance','model','subsystem','device','variable'))
                found=conn.execute('''SELECT id,unit FROM v2_channels WHERE namespace=? AND satellite_instance=?
                    AND model=? AND subsystem=? AND device=? AND variable=?''',identity).fetchone()
                if found and found[1]!=cfg['unit']:
                    raise ValueError(f'{name} 单位与现有通道不一致')
                identities[name]=(identity,found)
            if not commit:
                conn.rollback(); return result
            if before_commit and not before_commit():
                raise SourceImportCancelled('任务取消，未写入数据库')
            source_id=conn.execute('''INSERT INTO v2_sources(namespace,source_name,source_sha256,source_bytes,
                imported_utc,source_table_count,source_row_count) VALUES(?,?,?,?,?,?,?)''',
                ('upload_'+uuid4().hex,common['filename'],source_digest,source_size,
                 datetime.now(timezone.utc).isoformat(),1,len(rows))).lastrowid
            record_context(conn,source_id,context,quality)
            conn.executemany('INSERT INTO v2_source_raw_rows(source_id,source_ordinal,raw_row_json) VALUES(?,?,?)',
                             ((source_id,row[0],row[4]) for row in rows))
            imported=[]
            for index,name in enumerate(columns):
                cfg=configs[name]; identity,found=identities[name]
                cid=found[0] if found else conn.execute('''INSERT INTO v2_channels(namespace,satellite_instance,
                    model,subsystem,device,variable,unit) VALUES(?,?,?,?,?,?,?)''',(*identity,cfg['unit'])).lastrowid
                seg=conn.execute('''INSERT INTO v2_segments(source_id,source_table,channel_id,kind,role,
                    role_provenance,time_domain,usable_for_timeseries,coordinate_mode,frame_sequence_id,
                    columns_json,source_row_count)
                    VALUES(?,?,?,'telemetry',?,'user_explicit','frame',0,'frame',?,?,?)''',
                    (source_id,name,cid,common['role'],sequence,json.dumps(fields,ensure_ascii=False),len(rows))).lastrowid
                batch=[]
                for ordinal,frame,label,values,_ in rows:
                    value=values[index]
                    batch.append((seg,cid,ordinal,frame,value,json.dumps(value),label,json.dumps(label),
                                  'missing' if value is None else 'ok'))
                    if len(batch)>=500:
                        conn.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,frame_index,
                            time_domain,value_num,value_raw_json,label_int,label_raw_json,raw_row_json,quality_flag)
                            VALUES(?,?,?,?,'frame',?,?,?,?,'',?)''',batch);batch.clear()
                if batch:
                    conn.executemany('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,frame_index,
                        time_domain,value_num,value_raw_json,label_int,label_raw_json,raw_row_json,quality_flag)
                        VALUES(?,?,?,?,'frame',?,?,?,?,'',?)''',batch)
                imported.append({'source_column':name,'channel_id':cid,'segment_id':seg})
            conn.commit()
            result.update(source_id=source_id,channels=imported)
            return result
        except BaseException:
            conn.rollback();raise


def import_frame_csv(db_path,payload,*,commit=False,max_csv_bytes=4_000_000,
                     source_digest=None,source_size=None):
    raw=payload.get('csv')
    if not isinstance(raw,str) or not raw or len(raw.encode('utf-8'))>max_csv_bytes:
        raise ValueError('CSV 文本为空或超过大小限制')
    data=raw.encode('utf-8')
    return import_frame_csv_stream(db_path,payload,io.StringIO(raw.lstrip('\ufeff'),newline=''),
        commit=commit,source_digest=source_digest or hashlib.sha256(data).hexdigest(),
        source_size=source_size if source_size is not None else len(data))
