"""Non-destructive migration tests independent of the Web runtime."""
from pathlib import Path
import hashlib
import importlib.util
import sqlite3
import sys

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'rebuild_telemetry_v2.py'
spec = importlib.util.spec_from_file_location('aas_rebuild_telemetry_v2', MODULE)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fixture(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE aas_models(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE aas_subsystems(id INTEGER PRIMARY KEY,model_id INTEGER,name TEXT);
            CREATE TABLE aas_devices(id INTEGER PRIMARY KEY,subsystem_id INTEGER,name TEXT);
            CREATE TABLE aas_variables(id INTEGER PRIMARY KEY,device_id INTEGER,name TEXT);
            CREATE TABLE aas_series_catalog(table_name TEXT PRIMARY KEY,variable_id INTEGER,schema_kind TEXT);
            CREATE TABLE aas_table_meta(table_name TEXT PRIMARY KEY,declared_role TEXT);
            INSERT INTO aas_models VALUES(1,'GF-1');
            INSERT INTO aas_subsystems VALUES(1,1,'ADCS');
            INSERT INTO aas_devices VALUES(1,1,'RW1');
            INSERT INTO aas_variables VALUES(1,1,'speed');
            CREATE TABLE train_fragment_a(timestamp TEXT,value REAL,label INTEGER);
            CREATE TABLE train_fragment_b(timestamp TEXT,value REAL,label INTEGER);
            CREATE TABLE test_fragment(timestamp TEXT,value REAL,label INTEGER);
            CREATE TABLE wide(timestamp TEXT, f1 REAL, f2 REAL,label INTEGER);
            CREATE TABLE clean(timestamp TEXT,model TEXT,subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER,created_at TEXT);
            CREATE TABLE absolute_time(timestamp TEXT,value REAL,label INTEGER);
            INSERT INTO aas_series_catalog VALUES('train_fragment_a',1,'compact');
            INSERT INTO aas_series_catalog VALUES('train_fragment_b',1,'compact');
            INSERT INTO aas_series_catalog VALUES('test_fragment',1,'compact');
            INSERT INTO aas_series_catalog VALUES('absolute_time',1,'compact');
            INSERT INTO aas_table_meta VALUES('train_fragment_a','train');
            INSERT INTO aas_table_meta VALUES('train_fragment_b','train');
            INSERT INTO aas_table_meta VALUES('test_fragment','test');
            INSERT INTO train_fragment_a VALUES('2026-10-01 00:00:00',12.0,0);
            INSERT INTO train_fragment_a VALUES('2026-10-02 00:00:00',13.0,0);
            INSERT INTO train_fragment_b VALUES('2026-10-03 00:00:00',14.0,0);
            INSERT INTO test_fragment VALUES('2026-10-04 00:00:00',19.0,1);
            INSERT INTO wide VALUES('2026-10-01 00:00:00',5.0,6.0,0);
            INSERT INTO clean VALUES('2026-10-01 00:00:00','GF-1','ADCS','RW1','speed',12.0,0,'2026-10-10');
            INSERT INTO absolute_time VALUES('2026-10-02T08:00:00+08:00',12.0,0);
        ''')


def test_build_preserves_all_rows_and_does_not_touch_source(tmp_path):
    source=tmp_path/'sample.db';fixture(source)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    expected=mod.scan_source(source)
    target=tmp_path/'v2.sqlite'
    result=mod.build_sources([source],target)
    assert result['rows_preserved']==expected['rows']
    assert result['canonical_samples']+result['legacy_rows']==expected['rows']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    assert target.exists()
    with sqlite3.connect(target) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==6
        assert conn.execute('SELECT COUNT(*) FROM v2_legacy_rows').fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM v2_issues WHERE issue_code='unmapped_table'").fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM v2_segments WHERE source_table='wide' AND kind='unmapped'").fetchone()[0]==1
    with pytest.raises(FileExistsError):
        mod.build_sources([source],target)


def test_query_splices_selected_role_only_and_preserves_source(tmp_path):
    source=tmp_path/'sample.db';fixture(source)
    target=tmp_path/'v2.sqlite';mod.build_sources([source],target)
    rows=mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['train'])
    assert [r['value_num'] for r in rows]==[12.0,13.0,14.0]
    assert [r['source_table'] for r in rows]==['train_fragment_a','train_fragment_a','train_fragment_b']
    assert [r['value_num'] for r in mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['test'])]==[19.0]
    assert [r['value_num'] for r in mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['train'],start='2026-10-02 00:00:00',end='2026-10-03 00:00:00')]==[13.0,14.0]
    with pytest.raises(ValueError,match='时间基准'):
        mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['train','test','unspecified'])
    with pytest.raises(ValueError,match='roles'):
        mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed')


def test_time_is_not_assumed_utc():
    assert mod.parse_time('2026-10-01 00:00:00')[0]=='datetime_naive'
    assert mod.parse_time('0.000000000000e+00')[0]=='numeric_unspecified'
    assert mod.parse_time('2026-10-01T08:00:00+08:00')[3]==mod.parse_time('2026-10-01T00:00:00Z')[3]
    assert mod.parse_time('bad timestamp')[0]=='invalid'


def test_identical_stem_sources_are_rejected(tmp_path):
    p=tmp_path/'a';p.mkdir();q=tmp_path/'b';q.mkdir()
    fixture(p/'source.db');fixture(q/'source.db')
    with pytest.raises(ValueError,match='命名空间'):
        mod.build_sources([p/'source.db',q/'source.db'],tmp_path/'out.sqlite')
    assert not (tmp_path/'out.sqlite').exists()


def test_no_role_inference_promoted_to_trusted_training(tmp_path):
    source=tmp_path/'sample.db';fixture(source)
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE speed_train(timestamp TEXT, model TEXT, subsystem TEXT,device TEXT,variable TEXT,value REAL,label INTEGER)")
        db.execute("INSERT INTO speed_train VALUES('2026-10-07 00:00:00','GF-1','ADCS','RW1','speed',30,0)")
    target=tmp_path/'out.sqlite';mod.build_sources([source],target)
    assert len(mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['train']))==3
    rows=mod.query_channel(target,'sample','GF-1','ADCS','RW1','speed',roles=['train'],allow_inferred_roles=True)
    assert len(rows)==4
