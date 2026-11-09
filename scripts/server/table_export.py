"""Safe full-table CSV/ZIP exports for managed historical and simulation datasets.

All table names are resolved against SQLite schema, never interpolated from
unchecked request strings. Rows are written in batches to temporary disk files,
so exporting an entire table does not grow process memory with row count.
"""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from scripts.server.sqlite_guard import sqlite_connection as guarded_sqlite_connection
from scripts.server import series_catalog as catalog

_ALLOWED_COLUMNS = frozenset({"model", "subsystem", "device", "variable"})
_MAX_TABLES = 40


def _quoted(value: str) -> str:
    """Quote an identifier whose existence was verified from sqlite_master."""
    return '"' + value.replace('"', '""') + '"'


def list_exportable_tables(db_path: Path, *, simulation_db_path: Path) -> list[dict[str, Any]]:
    """List business tables, their clean variants, and simulation fault tables."""
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    with guarded_sqlite_connection(db_path, simulation_db_path=simulation_db_path) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        result = []
        for name in names:
            if not name or name.lower().startswith('sqlite_') or name.startswith('aas_'):
                continue
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(name)})")]
            # Internal registry/manifest tables are never exportable.
            if not _ALLOWED_COLUMNS.issubset(cols) and not catalog.is_compact(conn, name):
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {_quoted(name)}").fetchone()[0]
            kind = '清洁数据' if name.endswith('_clean') else ('模拟故障' if '_fault_' in name else '原始数据')
            result.append({'name': name, 'row_count': count, 'kind': kind})
    return result


def build_table_export(
    db_path: Path, table_names: list[str], *, simulation_db_path: Path
) -> tuple[BinaryIO, str, str]:
    """Return a seekable binary temporary stream, MIME type and download name.

    A single table yields a UTF-8 BOM CSV. Multiple tables yield a ZIP with
    one independent CSV per table. The caller owns and must close the stream.
    """
    if not isinstance(table_names, list) or not 1 <= len(table_names) <= _MAX_TABLES:
        raise ValueError(f"请选择 1–{_MAX_TABLES} 张数据表")
    if any(not isinstance(name, str) or not name for name in table_names):
        raise ValueError('数据表名称无效')
    if len(table_names) != len(set(table_names)):
        raise ValueError('不能重复导出相同数据表')
    available_items = list_exportable_tables(db_path, simulation_db_path=simulation_db_path)
    available = {item['name'] for item in available_items}
    invalid = set(table_names) - available
    if invalid:
        raise ValueError(f"数据表不存在或不允许导出：{', '.join(sorted(invalid))}")
    output = tempfile.TemporaryFile(mode='w+b')
    try:
        with guarded_sqlite_connection(db_path, simulation_db_path=simulation_db_path) as conn:
            def write_csv(binary_stream: BinaryIO, name: str) -> None:
                writer_stream = io.TextIOWrapper(
                    binary_stream, encoding='utf-8-sig', newline='', write_through=True
                )
                try:
                    cursor = conn.execute(f"SELECT * FROM {_quoted(name)}")
                    writer = csv.writer(writer_stream)
                    logical_cols = catalog.logical_columns(conn, name)
                    identity = catalog.signature(conn, name) if catalog.is_compact(conn, name) else None
                    physical_cols = [col[0] for col in cursor.description]
                    writer.writerow(logical_cols)
                    while batch := cursor.fetchmany(2048):
                        if identity:
                            for entry in batch:
                                projected = catalog.expand_known(dict(zip(physical_cols, entry)), identity, logical_cols)
                                writer.writerow([projected[key] for key in logical_cols])
                        else:
                            writer.writerows(batch)
                    writer_stream.flush()
                finally:
                    writer_stream.detach()  # Preserve the underlying output stream.

            if len(table_names) == 1:
                write_csv(output, table_names[0])
            else:
                with zipfile.ZipFile(output, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for index, current_name in enumerate(table_names, 1):
                        # Indexed filenames avoid path traversal and duplicate filesystem names.
                        with archive.open(f'table_{index:03d}.csv', 'w') as entry:
                            write_csv(entry, current_name)
                    manifest = '\n'.join(
                        f'table_{index:03d}.csv\t{name}'
                        for index, name in enumerate(table_names, 1)
                    )
                    archive.writestr('table_names.txt', manifest.encode('utf-8-sig'))
        output.seek(0)
        if len(table_names) == 1:
            return output, 'text/csv; charset=utf-8', 'table_export.csv'
        return output, 'application/zip', 'tables_export.zip'
    except (Exception, KeyboardInterrupt):
        output.close()
        raise


def build_logical_series_export(db_path: Path, identity: dict[str, str], *,
                                simulation_db_path: Path, start_time: Any = None,
                                end_time: Any = None) -> tuple[BinaryIO, str, str]:
    """Export one channel as a logical, time-sorted CSV + source manifest ZIP.

    Stream row batches to disk under a single consistent SQLite read snapshot.
    Never creates or overwrites business tables. Duplicate/conflict rules are
    exactly the same as the interactive telemetry-series endpoint.
    """
    import json
    from scripts.server.continuous_series import query_continuous_series, MAX_PAGE

    output = tempfile.TemporaryFile(mode='w+b')
    try:
        with guarded_sqlite_connection(db_path, simulation_db_path=simulation_db_path) as conn:
            conn.execute('BEGIN')
            info = query_continuous_series(db_path, identity, start_time=start_time,
                end_time=end_time, limit=0, simulation_db_path=simulation_db_path, connection=conn)
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open('telemetry.csv', 'w') as entry:
                    # Text wrapper flushes with UTF-8 BOM; binary cursor is
                    # owned by ZipFile, so detach rather than closing it.
                    stream = io.TextIOWrapper(entry, encoding='utf-8-sig', newline='', write_through=True)
                    try:
                        writer = csv.writer(stream)
                        writer.writerow(info['columns'])
                        total, offset = 0, 0
                        while total < info['matched_count']:
                            page = query_continuous_series(db_path, identity,
                                start_time=start_time, end_time=end_time, limit=MAX_PAGE, offset=offset,
                                simulation_db_path=simulation_db_path, connection=conn)
                            if page['matched_count'] != info['matched_count'] or not page['rows']:
                                raise ValueError('分页快照不一致，已中止导出')
                            for row in page['rows']:
                                writer.writerow([row[col] for col in info['columns']])
                            total += len(page['rows'])
                            offset += len(page['rows'])
                        stream.flush()
                    finally:
                        stream.detach()
                # digest is measured on the actual uncompressed CSV payload.
                # Reading through ZipFile here would consume extra I/O; SHA256
                # of the logical rows is provided by the execution registry for
                # training, while ZIP manifest is a selection/provenance record.
                manifest = {
                    'format': 'aas.logical_telemetry.v1',
                    'identity': info['identity'],
                    'time_selection': {'start': start_time, 'end': end_time},
                    'source_tables': info['sources'],
                    'row_count': info['matched_count'],
                    'duplicate_count': info['duplicate_count'],
                    'timestamp_kind': info['value_type'],
                    'columns': info['columns'],
                    'snapshot_semantics': 'SQLite read transaction; same-timestamp equal rows deduplicated',
                }
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            conn.commit()
        output.seek(0)
        return output, 'application/zip', 'logical_telemetry_export.zip'
    except BaseException:
        output.close()
        raise
