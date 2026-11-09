"""Backend-only regression: normalized telemetry, original labels, legacy and I/O."""
from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest

from scripts.server import series_catalog as catalog, services
from scripts.server.table_export import build_table_export, list_exportable_tables
from scripts.core.multi_table_fusion import fuse_sources
from scripts.core.data_loader import DataLoader
from scripts.migrate_series_metadata import migrate


def _source_csv(variable: str, values: list[float], model: str = 'TEST') -> str:
    lines = ['timestamp,model,subsystem,device,variable,value,label']
    lines += [f'2026-01-01 00:00:{i:02d},{model},ADCS,gyro,{variable},{v},{int(i == 4)}'
              for i, v in enumerate(values)]
    return '\n'.join(lines) + '\n'


def _managed_db(name: str):
    return services.PROJECT_ROOT / 'datasets' / name


def _mapping():
    return {key: {'type': 'column', 'value': key} for key in
            ['timestamp', 'model', 'subsystem', 'device', 'variable', 'value', 'label']}


def test_compact_import_and_logical_read_export_delete(tmp_path):
    path = _managed_db(f'catalog_regression_{tmp_path.name}.db')
    sqlite3.connect(path).close()
    try:
        for variable in ('wx', 'wy'):
            services.import_data(_source_csv(variable, [1, 2, 3, 4, 5, 6]),
                                 'csv', path.name, '', variable, _mapping(), data_role='test')
        with sqlite3.connect(path) as conn:
            for name in ('wx', 'wy'):
                assert catalog.columns(conn, name) == ['timestamp', 'value', 'label']
                assert catalog.signature(conn, name)['variable'] == name
                assert conn.execute(f'SELECT label FROM "{name}" WHERE value=5').fetchone() == (1,)
            aligned, meta = fuse_sources(conn, ['wx', 'wy'])
            assert len(aligned) == 6 and aligned[4]['label'] == 1
            assert meta['source_signatures']['wy']['variable'] == 'wy'
        browser = services.sqlite_tables(path)
        assert {item['name'] for item in browser} == {'wx', 'wy'}
        assert services.sqlite_hierarchy_options(path)['models'] == ['TEST']
        logical = services.sqlite_rows(path, 'wx')
        assert logical['columns'] == ['timestamp', 'model', 'subsystem', 'device', 'variable', 'value', 'label']
        assert logical['rows'][4]['variable'] == 'wx' and logical['rows'][4]['label'] == 1
        assert {item['name'] for item in list_exportable_tables(path, simulation_db_path=services.SIMULATION_DB_PATH)} == {'wx', 'wy'}
        handle, _, _ = build_table_export(path, ['wx'], simulation_db_path=services.SIMULATION_DB_PATH)
        with handle:
            rows = list(csv.DictReader(io.StringIO(handle.read().decode('utf-8-sig'))))
        assert len(rows) == 6 and rows[4]['model'] == 'TEST' and rows[4]['label'] == '1'
        # Same time windows and independent model input as the legacy structure.
        loader = DataLoader(path, train_ratio=.7)
        result = loader.load_and_split(table='wx')
        assert len(result.train_values) == 4 and len(result.test_values) == 2
        services.delete_history_table(path, 'wx')
        with sqlite3.connect(path) as conn:
            assert catalog.get(conn, 'wx') is None
            assert catalog.get(conn, 'wy') is not None
    finally:
        path.unlink(missing_ok=True)


def test_existing_table_identity_cannot_be_changed(tmp_path):
    path = _managed_db(f'catalog_identity_{tmp_path.name}.db')
    sqlite3.connect(path).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]),
                             'csv', path.name, '', 'main', _mapping(), data_role='test')
        with pytest.raises(ValueError, match='归属元信息'):
            services.import_data(_source_csv('wy', [1, 2, 3, 4, 5, 6]),
                                 'csv', path.name, '', 'main', _mapping(), data_role='test')
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM main').fetchone()[0] == 6
            assert catalog.signature(conn, 'main')['variable'] == 'wx'
        mixed = _source_csv('wx', [1, 2, 3, 4, 5, 6]).replace('ADCS,gyro,wx,3', 'ADCS,gyro,wy,3')
        with pytest.raises(ValueError, match='单参量表'):
            services.import_data(mixed, 'csv', path.name, '', 'wrong', _mapping())
    finally:
        path.unlink(missing_ok=True)


def test_legacy_migration_backup_and_row_identity_parity(tmp_path):
    path = _managed_db(f'catalog_migration_{tmp_path.name}.db')
    try:
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE good(id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, subsystem TEXT, device TEXT, variable TEXT, value REAL, label INTEGER, created_at TEXT)')
            conn.executemany('INSERT INTO good VALUES(?,?,?,?,?,?,?,?,?)',
                             [(i, str(i), 'M', 'ADCS', 'gyro', 'wx', float(i), i % 2, 'date') for i in range(1, 7)])
            conn.execute('CREATE TABLE mixed(timestamp TEXT,model TEXT,subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER)')
            conn.executemany('INSERT INTO mixed VALUES(?,?,?,?,?,?,?)',
                             [('1','M','ADCS','gyro','wx',1,0), ('2','M','ADCS','gyro','wy',2,1)])
            conn.execute('CREATE TABLE wide(timestamp TEXT,model TEXT,subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER, f1 REAL)')
            conn.execute("INSERT INTO wide VALUES('1','M','ADCS','gyro','wx',1,0,17)")
        preview = migrate(path, apply=False)
        assert preview['eligible'] == 1
        result = migrate(path, apply=True, backup_dir=tmp_path)
        assert result['converted'] == 1 and result['records_verified'] == 6
        assert Path(result['backup']).exists()
        with sqlite3.connect(path) as conn:
            assert catalog.columns(conn, 'good') == ['id', 'timestamp', 'value', 'label', 'created_at']
            assert set(catalog.IDENTITY).issubset(catalog.columns(conn, 'mixed'))
            assert set(catalog.IDENTITY).issubset(catalog.columns(conn, 'wide'))
            assert [r[0] for r in conn.execute('SELECT label FROM good ORDER BY id')] == [1, 0, 1, 0, 1, 0]
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert services.sqlite_rows(path, 'good')['rows'][0]['model'] == 'M'
        with sqlite3.connect(result['backup']) as backup:
            assert set(catalog.IDENTITY).issubset(catalog.columns(backup, 'good'))
            assert backup.execute('SELECT COUNT(*) FROM good').fetchone()[0] == 6
        # Re-running migration is idempotent; no second backup is needed.
        assert migrate(path, apply=True, backup_dir=tmp_path)['converted'] == 0
    finally:
        path.unlink(missing_ok=True)


def test_internal_catalog_is_not_exported_or_listed(tmp_path):
    path = _managed_db(f'catalog_hidden_{tmp_path.name}.db')
    sqlite3.connect(path).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]), 'csv', path.name, '',
                             'raw', _mapping(), data_role='test')
        assert catalog.CATALOG not in {entry['name'] for entry in services.sqlite_tables(path)}
        assert catalog.CATALOG not in {entry['name'] for entry in list_exportable_tables(path, simulation_db_path=services.SIMULATION_DB_PATH)}
        with pytest.raises(ValueError):
            services.delete_history_table(path, catalog.CATALOG)
    finally:
        path.unlink(missing_ok=True)


def test_compact_clean_table_inherits_identity_and_delete_cleans_registry(tmp_path):
    from scripts import visualization_server
    path = _managed_db(f'catalog_clean_{tmp_path.name}.db')
    sqlite3.connect(path).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]),
                             'csv', path.name, '', 'raw', _mapping(), data_role='test')
        response = visualization_server.run_data_preprocessing(path, 'raw',
                                                              {'normalize_method': 'none'})
        assert response['status'] == 'success'
        with sqlite3.connect(path) as conn:
            assert catalog.columns(conn, 'raw_clean') == ['timestamp', 'value', 'label']
            assert catalog.signature(conn, 'raw_clean')['variable'] == 'wx'
            assert [row[0] for row in conn.execute('SELECT label FROM raw_clean')] == [0, 0, 0, 0, 1, 0]
        assert services.sqlite_rows(path, 'raw_clean')['rows'][4]['variable'] == 'wx'
        assert visualization_server.delete_clean_table(path, 'raw')['status'] == 'success'
        with sqlite3.connect(path) as conn:
            assert catalog.get(conn, 'raw_clean') is None
    finally:
        path.unlink(missing_ok=True)


def test_compact_fault_injection_preserves_identity_and_removes_registry(tmp_path, monkeypatch):
    path = _managed_db(f'catalog_fault_{tmp_path.name}.db')
    sqlite3.connect(path).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]),
                             'csv', path.name, '', 'raw', _mapping(), data_role='test')
        monkeypatch.setattr(services, 'SIMULATION_DB_PATH', path)
        result = services.execute_fault_injection('raw', 'spike', {'severity': 0.5})
        assert result['status'] == 'success', result
        fault = result['fault_table_name']
        with sqlite3.connect(path) as conn:
            assert catalog.signature(conn, fault)['variable'] == 'wx'
        assert services.sqlite_rows(path, fault)['rows'][0]['model'] == 'TEST'
        assert services.delete_fault_table(fault)['status'] == 'success'
        with sqlite3.connect(path) as conn:
            assert catalog.get(conn, fault) is None
    finally:
        path.unlink(missing_ok=True)


def test_simulation_sync_copies_compact_identity(tmp_path, monkeypatch):
    source = _managed_db(f'catalog_source_{tmp_path.name}.db')
    target = _managed_db(f'catalog_target_{tmp_path.name}.db')
    sqlite3.connect(source).close()
    sqlite3.connect(target).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]),
                             'csv', source.name, '', 'raw', _mapping(), data_role='test')
        monkeypatch.setattr(services, 'HISTORY_SAMPLE_DB_PATH', source)
        monkeypatch.setattr(services, 'SIMULATION_DB_PATH', target)
        result = services.sync_simulation_db()
        assert result['status'] == 'success', result
        with sqlite3.connect(target) as conn:
            assert catalog.signature(conn, 'raw')['variable'] == 'wx'
            assert catalog.columns(conn, 'raw') == ['timestamp', 'value', 'label']
            assert conn.execute('SELECT COUNT(*) FROM raw').fetchone()[0] == 6
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def test_http_server_compatibility_facade_syncs_catalog_and_hierarchy(tmp_path, monkeypatch):
    from scripts import visualization_server as web_server
    source = _managed_db(f'catalog_http_source_{tmp_path.name}.db')
    target = _managed_db(f'catalog_http_target_{tmp_path.name}.db')
    sqlite3.connect(source).close()
    sqlite3.connect(target).close()
    try:
        services.import_data(_source_csv('wx', [1, 2, 3, 4, 5, 6]),
                             'csv', source.name, '', 'raw', _mapping(), data_role='test')
        assert web_server.sqlite_hierarchy_options(source)['variables'] == ['wx']
        monkeypatch.setattr(web_server, 'HISTORY_SAMPLE_DB_PATH', source)
        monkeypatch.setattr(web_server, 'SIMULATION_DB_PATH', target)
        result = web_server.sync_simulation_db()
        assert result['status'] == 'success', result
        with sqlite3.connect(target) as conn:
            assert catalog.signature(conn, 'raw')['variable'] == 'wx'
        status = web_server.get_simulation_status()
        assert status['exists'] is True and status['original_tables'] == 1
        assert status['total_rows'] == 6
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
