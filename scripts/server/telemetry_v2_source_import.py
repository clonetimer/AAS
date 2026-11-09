"""Read-only local-file importer for CSV.GZ / SQLite into the V2 channel pipeline.

Raw binary bytes never become a file path supplied by a browser. SQLite files
are staged in an isolated temporary directory and opened in immutable RO mode.
All writes go exclusively to the configured AAS V2 destination via the existing
atomic wide importer; previews execute the same collision and type checks.
"""
from __future__ import annotations

import csv
from contextlib import closing
import hashlib
import re
import io
import json
import math
from pathlib import Path
import sqlite3
import tempfile
from scripts.server.csv_format import (
    CSV_ENCODINGS, CSV_DELIMITERS, decode_csv as _shared_decode_csv,
    decompress_gzip as _shared_decompress_gzip, detect_delimiter as _shared_detect_delimiter,
    normalize_delimiter as _shared_normalize_delimiter, resolve_csv_format, normalize_delimiter,
    decompress_gzip_file, resolve_csv_stream_format,
)
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.server.import_cancellation import SourceImportCancelled
from scripts.server.csv_quality import analyze_csv_rows

MAX_UPLOAD_BYTES = 64_000_000
MAX_EXTRACTED_BYTES = 12_000_000
MAX_TABLES = 200
MAX_COLUMNS = 66  # time + label + 64 measurements


def _safe_filename(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 200 or name.strip() != name:
        raise ValueError('请选择合法的本地文件名')
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('文件名不能包含路径或控制字符')
    suffix = name.lower()
    if re.search(r'\.csv(?:\([0-9]+\))?\.gz$', suffix):
        return 'gzip'
    if suffix.endswith('.csv'):
        return 'csv'
    if suffix.endswith(('.sqlite', '.sqlite3', '.db')):
        return 'sqlite'
    raise ValueError('只支持 .csv、.csv.gz、.sqlite、.sqlite3、.db 文件')


def _bounded_plain_csv(raw: bytes, encoding='utf-8') -> str:
    if len(raw) > MAX_EXTRACTED_BYTES:
        raise ValueError('普通 CSV 超过 12MB，请分批导入')
    return _decode_csv(raw, encoding)


def _decode_csv(raw, encoding):
    return _shared_decode_csv(raw, encoding)


def _bounded_gzip_bytes(raw):
    return _shared_decompress_gzip(raw, MAX_EXTRACTED_BYTES)


def _detect_delimiter(text):
    return _shared_detect_delimiter(text)


def _normalize_csv_delimiter(text, delimiter):
    return _shared_normalize_delimiter(text, delimiter,
                                       max_rows=50_000, max_bytes=MAX_EXTRACTED_BYTES)


def _bounded_gzip(raw, encoding='utf-8'):
    return _decode_csv(_bounded_gzip_bytes(raw), encoding)


def _detect_delimiter(text):
    return _shared_detect_delimiter(text)


def _normalize_csv_delimiter(text, delimiter):
    return _shared_normalize_delimiter(text, delimiter,
                                       max_rows=50_000, max_bytes=MAX_EXTRACTED_BYTES)


def _csv_format_options(metadata, operation):
    encoding = metadata.get('csv_encoding')
    delimiter = metadata.get('csv_delimiter')
    if encoding not in (None, 'auto', *CSV_ENCODINGS):
        raise ValueError('CSV 编码只能选择自动识别、UTF-8 或 GB18030')
    if delimiter not in (None, 'auto', *CSV_DELIMITERS):
        raise ValueError('CSV 分隔符只能选择自动识别、逗号、分号或 TAB')
    if operation != 'inspect' and (
            encoding not in CSV_ENCODINGS or delimiter not in CSV_DELIMITERS
            or metadata.get('format_confirmed') is not True):
        raise ValueError('请先检查列并明确确认 CSV 编码与分隔符，再预检或导入')
    return encoding, delimiter



def _suggest_numeric_columns(text, delimiter, columns):
    # The advisory only samples the first 50 rows. Do not turn this heuristic
    # into a data-acceptance constraint; the importer still validates all rows.
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    next(reader)
    candidates = {name: False for name in columns if name not in
                  ('timestamp', 'time_s', 'frame', 'label', 'fault_label') and not name.startswith('aas_')}
    nonnumeric = set()
    rows = 0
    try:
        for values in reader:
            if len(values) != len(columns):
                rows += 1
                if rows >= 50:break
                continue  # inspect reports the row; full preview/commit still reject it
            rows += 1
            for index, name in enumerate(columns):
                if name not in candidates or name in nonnumeric:
                    continue
                cell = values[index].strip()
                if cell.lower() in ('', 'na', 'n/a', 'null', 'nan'):
                    continue
                try:
                    value = float(cell)
                    if not math.isfinite(value):
                        nonnumeric.add(name)
                    else:
                        candidates[name] = True
                except ValueError:
                    nonnumeric.add(name)
            if rows >= 50:
                break
    except csv.Error as exc:
        raise ValueError(f'CSV 第 {reader.line_num} 行引号/字段格式错误') from exc
    return [name for name, observed in candidates.items() if observed and name not in nonnumeric], rows



def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _table_catalog(conn):
    tables = conn.execute("""SELECT name,sql FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name""").fetchall()
    if len(tables) > MAX_TABLES:
        raise ValueError('SQLite 数据表超过 200 张，请预先拆分数据库')
    result = []
    for name, sql in tables:
        if not isinstance(name, str) or not sql or sql.lstrip().lower().startswith('create virtual table'):
            continue
        columns = [row[1] for row in conn.execute('PRAGMA table_info('+_quoted(name)+')')]
        if not columns or len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns):
            continue
        # The reader only understands time-aligned wide telemetry tables.
        time_key = next((x for x in ('timestamp', 'time_s') if x in columns), None)
        if not time_key or not [x for x in columns if x not in ('timestamp','time_s','label','fault_label')]:
            continue
        if not all(isinstance(x, str) and x and not x.startswith('__') for x in columns):
            continue
        sample = conn.execute('SELECT * FROM '+_quoted(name)+' LIMIT 2').fetchall()
        result.append({'name':name,'columns':columns,
                       'has_labels': 'label' in columns or 'fault_label' in columns,
                       'sample': [[_cell_preview(c) for c in row] for row in sample]})
    return result


def _cell_preview(value):
    if isinstance(value, bytes):
        return '[BLOB]'
    if value is None:
        return None
    return str(value)[:90]


def _selected_measurements(text: str, selected: object, *,
                           time_column: str | None = None,
                           label_column: str | None = None,
                           frame_column: str | None = None,
                           coordinate_mode: str = 'time') -> str:
    """Project only explicitly selected sensor columns, preserving source hash."""
    if not isinstance(selected, list) or not 1 <= len(selected) <= 64 or any(
            not isinstance(x,str) or not x or x.startswith('aas_') for x in selected
    ) or len(set(selected)) != len(selected):
        raise ValueError('需要显式指定 1～64 个不重复的数值遥测参量')
    reader=csv.DictReader(io.StringIO(text,newline=''),strict=True)
    cols=reader.fieldnames or []
    time=(time_column if time_column is not None else next((name for name in ('time_s','timestamp') if name in cols),None))
    frame=(frame_column if frame_column is not None else ('frame' if 'frame' in cols else None)) if coordinate_mode=='frame' else None
    label=(label_column if label_column is not None else next((name for name in ('label','fault_label') if name in cols),None))
    if coordinate_mode=='frame':
        time=None
    elif time not in cols:
        raise ValueError('指定的时间列不存在或源 CSV 缺少时间列')
    if label and label not in cols:
        raise ValueError('指定的标签列不存在')
    if not set(selected).issubset(cols) or time in selected or frame in selected or label in selected or any(
            x in selected for x in ('label', 'fault_label')):
        raise ValueError('选择参量必须来自 CSV 测量列且不能包括时间/标签')
    output=io.StringIO(newline='')
    projected=[*([time] if time else []),*([frame] if frame else []),*([label] if label else []),*[col for col in cols if col in selected]]
    writer=csv.DictWriter(output,fieldnames=projected,lineterminator='\n',extrasaction='ignore')
    writer.writeheader()
    for i,row in enumerate(reader):
        if i>=50000 or None in row or any(value is None for value in row.values()):
            raise ValueError('CSV 行数超出 50000 或单行字段不完整')
        writer.writerow(row)
        if output.tell()>MAX_EXTRACTED_BYTES:
            raise ValueError('所选 CSV 数据超出导入上限')
    return output.getvalue()


def _sqlite_csv(conn, table, catalog):
    selected = next((item for item in catalog if item['name'] == table), None)
    if selected is None:
        raise ValueError('请选择列表中存在且符合遥测结构的数据表')
    data = io.StringIO(newline='')
    writer = csv.writer(data, lineterminator='\n')
    writer.writerow(selected['columns'])
    total = 0
    # LIMIT one extra row: never accept a silently truncated table.
    for row in conn.execute('SELECT * FROM '+_quoted(table)+' LIMIT 50001'):
        total += 1
        if total > 50000:
            raise ValueError('所选 SQLite 表超过 50000 个采样，请先按时间拆分')
        for value in row:
            if value is not None and type(value) not in (str, int, float):
                raise ValueError('所选 SQLite 表包含非标量数值或 BLOB，不支持自动导入')
        writer.writerow(['' if c is None else c for c in row])
        if data.tell() > MAX_EXTRACTED_BYTES:
            raise ValueError('所选 SQLite 数据表转换后超过 12MB，请先按时间拆分')
    if not total:
        raise ValueError('所选 SQLite 数据表为空')
    return data.getvalue(), selected


def _sqlite_operation(raw, table, *, inspect=False):
    if not raw.startswith(b'SQLite format 3\x00'):
        raise ValueError('文件头不是 SQLite 3 数据库')
    with tempfile.TemporaryDirectory(prefix='aas-readonly-import-') as dirname:
        path = Path(dirname) / 'external.sqlite'
        path.write_bytes(raw)
        # immutable=1 prevents creating -wal or -shm on the uploaded artifact;
        # only a complete SQLite database snapshot is supported.
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True, timeout=3)) as conn:
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('PRAGMA query_only=ON')
            # Bounded SQLite VM steps to avoid pathological user-supplied files.
            budget = [0]
            def limiter():
                budget[0] += 1
                return int(budget[0] > 160)
            conn.set_progress_handler(limiter, 100000)
            catalog = _table_catalog(conn)
            if inspect:
                return {'tables':catalog}
            return _sqlite_csv(conn, table, catalog)


def source_operation(target_db:Path, raw:bytes, metadata:dict):
    """Inspect, preview or import; never mutate uploaded source bytes."""
    if not isinstance(metadata, dict):
        raise ValueError('导入元数据必须是 JSON 对象')
    operation = metadata.get('operation')
    if operation not in ('inspect', 'preview', 'commit'):
        raise ValueError('操作只能是 inspect、preview 或 commit')
    name = metadata.get('filename')
    kind = _safe_filename(name)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise ValueError('文件不能超过 64MB 且不能为空')
    if kind in ('gzip','csv'):
        encoding, delimiter = _csv_format_options(metadata, operation)
        csv_bytes = _bounded_gzip_bytes(raw) if kind == 'gzip' else raw
        text, encoding, delimiter = resolve_csv_format(
            csv_bytes, encoding or 'auto', delimiter or 'auto',
            max_output_bytes=MAX_EXTRACTED_BYTES)
        if operation == 'inspect':
            try:
                columns = next(csv.reader(io.StringIO(text),delimiter=delimiter, strict=True))
            except (csv.Error, StopIteration) as exc:
                raise ValueError('CSV 文件表头无法读取') from exc
            numeric, sampled = _suggest_numeric_columns(text, delimiter, columns)
            try:
                reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
                next(reader)
                rows = []
                for row in reader:
                    if len(rows) >= 50_000:
                        raise ValueError('CSV 质量检查最多支持 50000 帧')
                    rows.append(row)
            except csv.Error as exc:
                raise ValueError(f'CSV 第 {reader.line_num} 行结构不完整') from exc
            quality = analyze_csv_rows(columns, rows,
                time_column=metadata.get('time_column'),
                label_column=metadata.get('label_column'),frame_column=metadata.get('frame_column'))
            return {'quality': quality,
                    'time_candidates': quality['time_candidates'],
                    'label_candidates': quality['label_candidates'],
                    'format':'csv.gz' if kind=='gzip' else 'csv','columns':columns,
                    'has_labels': 'label' in columns or 'fault_label' in columns,
                    'uncompressed_bytes':len(csv_bytes),'csv_encoding':encoding,
                    'csv_delimiter':delimiter, 'format_review_required':True,
                    'numeric_columns':numeric, 'sampled_rows':sampled}
        text = normalize_delimiter(text, delimiter, max_rows=50_000,
                                   max_bytes=MAX_EXTRACTED_BYTES)
        selected_table = None
    else:
        if operation == 'inspect':
            return {'format':'sqlite', **_sqlite_operation(raw, None, inspect=True)}
        selected_table = metadata.get('table')
        if not isinstance(selected_table, str) or not 1 <= len(selected_table) <= 128:
            raise ValueError('SQLite 必须先选择数据表')
        text, _selected = _sqlite_operation(raw, selected_table)
    data = metadata.get('config')
    if not isinstance(data, dict):
        raise ValueError('导入前必须填写用途、时域和列映射')
    payload = {**data}
    selected = payload.pop('selected_columns',None)
    bind = payload.pop('bind_digest_group',False)
    if selected is not None:
        if payload.get('label_column') == '' and payload.get('role') == 'train':
            source_rows = csv.DictReader(io.StringIO(text, newline=''), strict=True)
            known_labels = [x for x in ('label', 'fault_label') if x in (source_rows.fieldnames or [])]
            if any(row.get(col, '').strip() != '0' for row in source_rows for col in known_labels):
                raise ValueError('原始 CSV 已存在故障标签或无法核验的标签，不能忽略后作为全部正常训练数据')
        text = _selected_measurements(text, selected,
            time_column=payload.get('time_column'),label_column=payload.get('label_column'),
            frame_column=payload.get('frame_column'),coordinate_mode=payload.get('coordinate_mode','time'))
    if bind is True:
        if payload.get('import_context') is not None:
            raise ValueError('摘要组绑定不能与自定义运行元数据同时使用')
        role=payload.get('role')
        if role not in ('train','test'):
            raise ValueError('摘要组绑定只接受训练或测试用途')
        token=hashlib.sha256(raw).hexdigest()[:24]
        columns=next(csv.reader(io.StringIO(text)))
        payload['import_context']={'run_token':token,'group_id':token,
            'dataset_split':'train' if role=='train' else 'validation',
            'run_role':'NORMAL' if role=='train' else None,
            'origin_type':'other',
            'label_scope':'run' if (payload.get('label_column') if payload.get('label_column') is not None else
                next((x for x in ('label', 'fault_label') if x in columns), None)) else 'unknown'}
    elif bind is not False:
        raise ValueError('数据来源摘要组绑定开关格式无效')
    payload.update(csv=text,filename=name,require_explicit_mapping=True)
    # A source table is trusted only if it came from the read-only catalog.
    payload.pop('source_table_name', None)
    if selected_table:
        payload['source_table_name'] = selected_table
    result = import_wide_csv(target_db, payload, commit=operation == 'commit',
                             max_csv_bytes=MAX_EXTRACTED_BYTES,
                             source_digest=hashlib.sha256(raw).hexdigest(),source_size=len(raw))
    result['source_format'] = 'csv.gz' if kind == 'gzip' else 'csv' if kind == 'csv' else 'sqlite'
    if selected_table:
        result['source_table'] = selected_table
    result['original_file_bytes'] = len(raw)
    return result

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _suggest_numeric_columns_file(path: Path, encoding: str, delimiter: str, columns: list[str]):
    candidates = {name: False for name in columns if name not in
                  ('timestamp', 'time_s', 'frame', 'label', 'fault_label') and not name.startswith('aas_')}
    nonnumeric = set()
    rows = 0
    try:
        with Path(path).open('r', encoding=CSV_ENCODINGS[encoding], newline='') as handle:
            reader = csv.reader(handle, delimiter=delimiter, strict=True)
            next(reader)
            for values in reader:
                rows += 1
                if len(values) != len(columns):
                    if rows >= 50:
                        break
                    continue
                for index, name in enumerate(columns):
                    if name not in candidates or name in nonnumeric:
                        continue
                    cell = values[index].strip()
                    if cell.lower() in ('', 'na', 'n/a', 'null', 'nan'):
                        continue
                    try:
                        value = float(cell)
                        if not math.isfinite(value):
                            nonnumeric.add(name)
                        else:
                            candidates[name] = True
                    except ValueError:
                        nonnumeric.add(name)
                if rows >= 50:
                    break
    except (csv.Error, UnicodeError) as exc:
        raise ValueError('CSV 前 50 行无法按当前格式解析') from exc
    return [name for name, observed in candidates.items() if observed and name not in nonnumeric], rows


def _sqlite_operation_file(path: Path, table, *, inspect=False):
    path = Path(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=3)) as conn:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA query_only=ON')
        budget = [0]

        def limiter():
            budget[0] += 1
            return int(budget[0] > 160)

        conn.set_progress_handler(limiter, 100000)
        catalog = _table_catalog(conn)
        if inspect:
            return {'tables': catalog}
        return _sqlite_csv(conn, table, catalog)


def source_operation_file(target_db: Path, source_path: Path, metadata: dict, *,
                          source_digest: str | None = None, source_size: int | None = None,
                          progress=None, cancel_requested=None, before_commit=None) -> dict:
    """Run inspect/preview/commit from a server-staged file without loading CSV bytes.

    CSV.GZ is inflated incrementally to a bounded temporary file. CSV rows are
    parsed once and written to a temporary SQLite staging database by the wide
    importer. The original upload is hashed and its byte size is retained in
    the V2 source record; temporary source and staging files are removed on exit.
    """
    from scripts.server.telemetry_v2_wide_import import import_wide_csv_stream

    if not isinstance(metadata, dict):
        raise ValueError('导入元数据必须是 JSON 对象')
    operation = metadata.get('operation')
    if operation not in ('inspect', 'preview', 'commit'):
        raise ValueError('操作只能是 inspect、preview 或 commit')
    name = metadata.get('filename')
    kind = _safe_filename(name)
    source = Path(source_path)
    if source.is_symlink() or not source.is_file():
        raise ValueError('服务端暂存文件不存在或不是普通文件')
    size = source.stat().st_size
    if not 0 < size <= MAX_UPLOAD_BYTES:
        raise ValueError('文件不能超过 64MB 且不能为空')
    digest = source_digest or _sha256_file(source)
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('原始文件摘要非法')
    if source_size is not None and source_size != size:
        raise ValueError('上传文件大小与暂存文件不一致')
    source_size = size

    def emit(value, message):
        if progress is not None:
            progress(value, message, 'csv_import')

    def check_cancel():
        if cancel_requested is not None and cancel_requested():
            raise SourceImportCancelled('用户取消 CSV 导入')

    check_cancel()
    if kind == 'sqlite':
        if operation == 'inspect':
            return {'format': 'sqlite', **_sqlite_operation_file(source, None, inspect=True)}
        selected_table = metadata.get('table')
        if not isinstance(selected_table, str) or not 1 <= len(selected_table) <= 128:
            raise ValueError('SQLite 必须先选择数据表')
        text, _selected = _sqlite_operation_file(source, selected_table)
        payload = metadata.get('config')
        if not isinstance(payload, dict):
            raise ValueError('导入前必须填写用途、时域和列映射')
        payload = {**payload, 'csv': text, 'filename': name, 'require_explicit_mapping': True,
                   'source_table_name': selected_table}
        result = import_wide_csv(target_db, payload, commit=operation == 'commit',
                                 max_csv_bytes=MAX_EXTRACTED_BYTES,
                                 source_digest=digest, source_size=source_size)
        result['source_format'] = 'sqlite'
        result['source_table'] = selected_table
        result['original_file_bytes'] = source_size
        return result

    encoding, delimiter = _csv_format_options(metadata, operation)
    with tempfile.TemporaryDirectory(prefix='aas-source-csv-') as dirname:
        csv_path = source
        if kind == 'gzip':
            csv_path = Path(dirname) / 'source.csv'
            try:
                decompressed_size = decompress_gzip_file(
                    source, csv_path, MAX_EXTRACTED_BYTES,
                    cancel_requested=cancel_requested,
                    progress=lambda done, total: emit(
                        15 + int(10 * done / max(1, total)), '正在安全解压 CSV.GZ'))
            except InterruptedError as exc:
                raise SourceImportCancelled('用户取消 GZIP 解压') from exc
        else:
            decompressed_size = size
            if decompressed_size > MAX_EXTRACTED_BYTES:
                raise ValueError('普通 CSV 超过 12MB，请分批导入')

        encoding, delimiter = resolve_csv_stream_format(csv_path, encoding or 'auto', delimiter or 'auto')
        emit(30, '文件已暂存，正在检查 CSV 表头与数据质量')
        if operation == 'inspect':
            try:
                with csv_path.open('r', encoding=CSV_ENCODINGS[encoding], newline='') as handle:
                    reader = csv.reader(handle, delimiter=delimiter, strict=True)
                    columns = next(reader)
            except (csv.Error, StopIteration, UnicodeError) as exc:
                raise ValueError('CSV 文件表头无法读取') from exc
            numeric, sampled = _suggest_numeric_columns_file(csv_path, encoding, delimiter, columns)
            try:
                with csv_path.open('r', encoding=CSV_ENCODINGS[encoding], newline='') as handle:
                    reader = csv.reader(handle, delimiter=delimiter, strict=True)
                    next(reader)
                    quality = analyze_csv_rows(
                        columns, reader, time_column=metadata.get('time_column'),
                        label_column=metadata.get('label_column'),frame_column=metadata.get('frame_column'))
            except csv.Error as exc:
                raise ValueError(f'CSV 第 {reader.line_num} 行结构不完整') from exc
            emit(95, 'CSV 文件检查完成')
            return {'quality': quality,
                    'time_candidates': quality['time_candidates'],
                    'label_candidates': quality['label_candidates'],
                    'format': 'csv.gz' if kind == 'gzip' else 'csv',
                    'columns': columns,
                    'has_labels': 'label' in columns or 'fault_label' in columns,
                    'uncompressed_bytes': decompressed_size,
                    'csv_encoding': encoding, 'csv_delimiter': delimiter,
                    'format_review_required': True,
                    'numeric_columns': numeric, 'sampled_rows': sampled}

        payload = metadata.get('config')
        if not isinstance(payload, dict):
            raise ValueError('导入前必须填写用途、时域和列映射')
        payload = dict(payload)
        bind = payload.pop('bind_digest_group', False)
        if bind is True:
            if payload.get('import_context') is not None:
                raise ValueError('摘要组绑定不能与自定义运行元数据同时使用')
            role = payload.get('role')
            if role not in ('train', 'test'):
                raise ValueError('摘要组绑定只接受训练或测试用途')
            token = digest[:24]
            source_fields = None
            with csv_path.open('r', encoding=CSV_ENCODINGS[encoding], newline='') as handle:
                source_fields = next(csv.reader(handle, delimiter=delimiter, strict=True))
            label_name = payload.get('label_column')
            if label_name is None:
                label_name = next((value for value in ('label', 'fault_label') if value in source_fields), None)
            payload['import_context'] = {
                'run_token': token, 'group_id': token,
                'dataset_split': 'train' if role == 'train' else 'validation',
                'run_role': 'NORMAL' if role == 'train' else None,
                'origin_type': 'other',
                'label_scope': 'run' if label_name else 'unknown',
            }
        elif bind is not False:
            raise ValueError('数据来源摘要组绑定开关格式无效')
        payload.update(filename=name, require_explicit_mapping=True)
        check_cancel()
        with csv_path.open('r', encoding=CSV_ENCODINGS[encoding], newline='') as handle:
            result = import_wide_csv_stream(
                target_db, payload, handle, delimiter=delimiter,
                commit=operation == 'commit',
                source_digest=digest, source_size=source_size,
                progress=progress, cancel_requested=cancel_requested,
                before_commit=before_commit if operation == 'commit' else None)
        result['source_format'] = 'csv.gz' if kind == 'gzip' else 'csv'
        result['original_file_bytes'] = source_size
        result['uncompressed_bytes'] = decompressed_size
        result['csv_encoding'] = encoding
        result['csv_delimiter'] = delimiter
        result['compression'] = 'gzip' if kind == 'gzip' else 'none'
        result['format_review_required'] = True
        return result
