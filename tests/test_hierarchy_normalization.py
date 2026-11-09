"""Backend-only regression for FK hierarchy normalization and safe migration."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.server import series_catalog as catalog, services
from scripts.migrate_series_hierarchy import migrate
from scripts.core.multi_table_fusion import fuse_sources
from scripts.server.table_export import build_table_export


def _make_table(conn: sqlite3.Connection, table: str, model='M', subsystem='ADCS',
                device='gyro', variable='x', *, compact=False):
    fields = '' if compact else ',model TEXT,subsystem TEXT,device TEXT,variable TEXT'
    conn.execute(f'CREATE TABLE "{table}"(timestamp TEXT,value REAL,label INTEGER{fields})')
    rows = [(str(i), float(i), i % 2) for i in range(6)]
    if compact:
        conn.executemany(f'INSERT INTO "{table}" VALUES (?,?,?)', rows)
        catalog.register(conn, table, dict(model=model, subsystem=subsystem, device=device,
                                           variable=variable), 'compact')
    else:
        conn.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?,?,?,?)',
                         [(t, v, l, model, subsystem, device, variable) for t, v, l in rows])


def test_new_catalog_normalizes_shared_paths_and_same_named_channels():
    with sqlite3.connect(':memory:') as conn:
        _make_table(conn, 'a', variable='temperature', compact=True)
        _make_table(conn, 'b', variable='temperature', compact=True)
        _make_table(conn, 'c', device='wheel', variable='temperature', compact=True)
        _make_table(conn, 'd', model='M2', variable='temperature', compact=True)
        assert catalog.is_normalized(conn)
        assert set(catalog.columns(conn, catalog.CATALOG)) == {'table_name','variable_id','schema_kind'}
        expected = dict(zip(catalog.HIERARCHY_TABLES, [2, 2, 3, 3]))
        assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in expected} == expected
        a, b, c = [conn.execute('SELECT variable_id FROM aas_series_catalog WHERE table_name=?',
                                (name,)).fetchone()[0] for name in ('a','b','c')]
        assert a == b and a != c
        assert catalog.signature(conn, 'c')['device'] == 'wheel'
        assert catalog.logical_columns(conn, 'a') == ['timestamp',*catalog.IDENTITY,'value','label']
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_prune_preserves_shared_ancestry_and_cleans_unused_nodes():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        _make_table(conn, 'a', compact=True)
        _make_table(conn, 'b', compact=True)
        conn.execute('DELETE FROM aas_series_catalog WHERE table_name=?', ('a',))
        catalog.prune_unused(conn)
        assert conn.execute('SELECT count(*) FROM aas_variables').fetchone()[0] == 1
        conn.execute('DELETE FROM aas_series_catalog WHERE table_name=?', ('b',))
        catalog.prune_unused(conn)
        assert all(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] == 0
                   for t in catalog.HIERARCHY_TABLES)


def test_foreign_keys_enforced_via_managed_connection(tmp_path):
    path = services.PROJECT_ROOT / 'datasets' / f'normalized_fk_{tmp_path.name}.db'
    try:
        with services.guarded_sqlite_connection(path) as conn:
            _make_table(conn, 'data', compact=True)
            conn.commit()
            assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM aas_variables WHERE id IN '
                             '(SELECT variable_id FROM aas_series_catalog)')
            conn.rollback()
    finally:
        path.unlink(missing_ok=True)


def test_legacy_reads_unchanged_then_explicit_migration_backups(tmp_path):
    path = services.PROJECT_ROOT / 'datasets' / f'normalize_legacy_{tmp_path.name}.db'
    try:
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE aas_series_catalog ('
                         'table_name TEXT PRIMARY KEY,model TEXT,subsystem TEXT,'
                         'device TEXT,variable TEXT,schema_kind TEXT)')
            _make_table(conn,'a',compact=True)
            _make_table(conn,'b',variable='y',compact=True)
            _make_table(conn,'legacy')
        with sqlite3.connect(path) as conn:
            assert not catalog.is_normalized(conn)
            assert catalog.get(conn,'a')['variable'] == 'x'
            assert catalog.normalize_hierarchy(conn,dry_run=True)['converted'] == 2
        assert migrate(path)['converted'] == 2
        result = migrate(path,apply=True,backup_dir=tmp_path)
        assert result['converted'] == 2 and result['sample_rows_verified'] == 18
        with sqlite3.connect(result['backup']) as backup:
            assert not catalog.is_normalized(backup)
            assert catalog.get(backup,'b')['variable'] == 'y'
        with sqlite3.connect(path) as conn:
            assert catalog.is_normalized(conn)
            assert catalog.signature(conn,'a')['variable'] == 'x'
            assert catalog.signature(conn,'b')['variable'] == 'y'
            assert not catalog.is_compact(conn,'legacy')
            data, _ = fuse_sources(conn,['a','b'])
            assert len(data) == 6
        assert migrate(path,apply=True,backup_dir=tmp_path)['already_normalized']
    finally:
        path.unlink(missing_ok=True)


def test_migration_rejects_invalid_legacy_registry_before_changing_schema():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE aas_series_catalog ('
                     'table_name TEXT PRIMARY KEY,model TEXT,subsystem TEXT,'
                     'device TEXT,variable TEXT,schema_kind TEXT)')
        _make_table(conn,'raw',compact=True)
        conn.execute('UPDATE aas_series_catalog SET device=? WHERE table_name=?',('other','raw'))
        # Existing flat catalog is authoritative for compact samples; legacy
        # identities cannot be recovered from compact physical rows. Invalid
        # legacy schemas are nevertheless detected through actual table checks.
        conn.execute('UPDATE aas_series_catalog SET schema_kind=?',('legacy',))
        with pytest.raises(ValueError):
            catalog.normalize_hierarchy(conn,dry_run=False)
        assert not catalog.is_normalized(conn)
        assert catalog.get(conn,'raw')['schema_kind'] == 'legacy'


def test_corrupt_normalized_fk_detected_even_if_sqlite_foreign_keys_disabled():
    with sqlite3.connect(':memory:') as conn:
        _make_table(conn, 'raw', compact=True)
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('UPDATE aas_series_catalog SET variable_id=9876543')
        with pytest.raises(ValueError, match='参量目录'):
            catalog.signature(conn, 'raw')
        with pytest.raises(ValueError, match='外键'):
            catalog.validate_hierarchy(conn)


def test_sample_databases_readable_before_or_after_explicit_hierarchy_upgrade():
    for path in (services.HISTORY_SAMPLE_DB_PATH, services.SIMULATION_DB_PATH):
        with sqlite3.connect(path) as conn:
            if catalog.is_normalized(conn):
                catalog.validate_hierarchy(conn)
            else:
                # Incremental installations preserve the old DB until the
                # operator runs the explicit migration tool.
                assert catalog.normalize_hierarchy(conn, dry_run=True)['converted'] >= 1
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            names = [row[0] for row in conn.execute('SELECT table_name FROM aas_series_catalog LIMIT 2')]
            for name in names:
                assert 'value' in catalog.columns(conn,name)
                assert catalog.signature(conn,name)
                assert conn.execute(f'SELECT count(*) FROM {catalog.quote(name)}').fetchone()[0] > 0


def test_migration_does_not_overwrite_existing_backup(tmp_path):
    path = services.PROJECT_ROOT / 'datasets' / f'normalize_backup_{tmp_path.name}.db'
    backup = tmp_path / f'{path.name}.before-hierarchy-normalization.bak'
    try:
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE aas_series_catalog ('
                         'table_name TEXT PRIMARY KEY,model TEXT,subsystem TEXT,'
                         'device TEXT,variable TEXT,schema_kind TEXT)')
            _make_table(conn, 'raw', compact=True)
        backup.write_bytes(b'existing backup must never be overwritten')
        with pytest.raises(FileExistsError, match='备份'):
            migrate(path, apply=True, backup_dir=tmp_path)
        assert backup.read_bytes() == b'existing backup must never be overwritten'
        with sqlite3.connect(path) as conn:
            assert not catalog.is_normalized(conn)
            assert catalog.get(conn, 'raw')['variable'] == 'x'
    finally:
        path.unlink(missing_ok=True)
