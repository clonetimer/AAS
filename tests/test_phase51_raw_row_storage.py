"""Phase51 lossless wide-row deduplication and independent-copy compaction."""
import gzip
import hashlib
import sqlite3

import pytest

from scripts.rebuild_telemetry_v2 import create_v2
from scripts.server.telemetry_v2_batch_import import import_file
from scripts.server.telemetry_v2_raw_rows import resolved_raw_row, raw_store_health
from scripts.server.telemetry_v2_wide_import import import_wide_csv
from scripts.compact_v2_raw_rows import compact
from scripts.server.telemetry_v2_access import query_charts


def blank(tmp_path):
    path=tmp_path/'target.sqlite'
    with sqlite3.connect(path) as db:create_v2(db)
    return path


def config():
    return {'namespace':'source-A','model':'SIM','subsystem':'ADCS','device':'RW',
            'unit':'1','role':'test','time_domain':'numeric_unspecified',
            'time_reference':'mission_seconds','time_unit':'s',
            'unlabeled_test_confirmed':True,'auto_map':True}


def input_gz(tmp_path):
    path=tmp_path/'wide.csv.gz'
    with gzip.open(path,'wt') as file:
        file.write('time_s,adcs.pointing_error_deg,eps.bus_load_power_w\n')
        for i in range(120):file.write(f'{i},{i+1},{i*2}\n')
    return path


def test_stream_import_shared_rows_roundtrip_and_labels(tmp_path):
    db=blank(tmp_path)
    src=input_gz(tmp_path)
    before=hashlib.sha256(src.read_bytes()).hexdigest()
    out=import_file(db,src,config())
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_source_raw_rows').fetchone()[0]==120
        assert conn.execute("SELECT COUNT(*) FROM v2_samples WHERE raw_row_json=''").fetchone()[0]==240
        assert raw_store_health(conn)=={'shared_rows':120,'references':240,'dangling_refs':0}
        sid=conn.execute('SELECT id FROM v2_samples ORDER BY id LIMIT 1').fetchone()[0]
        assert 'adcs.pointing_error_deg' in resolved_raw_row(conn,sid)
        assert conn.execute('SELECT raw_row_json FROM v2_sample_raw_resolved WHERE sample_id=?',
                            (sid,)).fetchone()[0] == resolved_raw_row(conn,sid)
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int IS NULL').fetchone()[0]==240
        channel=conn.execute('SELECT id FROM v2_channels ORDER BY id LIMIT 1').fetchone()[0]
    assert query_charts(db,role='test',channel_ids=[channel])['series'][0]['matched_count']==120
    assert import_file(db,src,config())['status']=='skipped'
    assert hashlib.sha256(src.read_bytes()).hexdigest()==before
    assert out['quality']['storage_estimate_kind'].startswith('conservative_shared_')


def test_wide_http_import_stores_raw_once(tmp_path):
    db=blank(tmp_path)
    payload={**config(), 'filename':'normal.csv','csv':
             'timestamp,a,b,label\n0,1,10,0\n1,2,20,1\n2,3,30,1\n',
             'column_metadata':{
               'a':{'subsystem':'ADCS','device':'rw','variable':'speed','unit':'1'},
               'b':{'subsystem':'EPS','device':'battery','variable':'voltage','unit':'V'}}}
    response=import_wide_csv(db,payload,commit=True)
    assert response['row_count']==3
    with sqlite3.connect(db) as conn:
        assert raw_store_health(conn)=={'shared_rows':3,'references':6,'dangling_refs':0}
        a,b=conn.execute('SELECT id FROM v2_samples ORDER BY id LIMIT 2').fetchall()
        assert '"a": "1"' in resolved_raw_row(conn,a[0])
        assert '"label": "1"' in resolved_raw_row(conn,b[0])


def _force_old_inline(db):
    """Create an old-style wide V2 database from a verified fresh source."""
    with sqlite3.connect(db) as conn:
        conn.execute('''UPDATE v2_samples SET raw_row_json=(
            SELECT r.raw_row_json FROM v2_segments g
            JOIN v2_source_raw_rows r ON r.source_id=g.source_id
                AND r.source_ordinal=v2_samples.source_ordinal
            WHERE g.id=v2_samples.segment_id)''')
        conn.execute('DROP TABLE v2_source_raw_rows')
        conn.commit()
        conn.execute('VACUUM')


def test_old_database_copy_compaction_roundtrip_and_rollback(tmp_path):
    db=blank(tmp_path); src=input_gz(tmp_path)
    import_file(db,src,config())
    _force_old_inline(db)
    hash_before=hashlib.sha256(db.read_bytes()).hexdigest()
    with sqlite3.connect(db) as conn:
        original_raw={row[0]:resolved_raw_row(conn,row[0]) for row in conn.execute('SELECT id FROM v2_samples')}
        original_labels=[r[0] for r in conn.execute('SELECT label_int FROM v2_samples ORDER BY id')]
    report=compact(db,tmp_path/'small.sqlite')
    assert report['status']=='created'
    assert not (tmp_path/'small.sqlite.partial').exists()
    assert report['output_bytes']<=report['source_bytes']  # small fixtures may share the same SQLite page floor
    assert hashlib.sha256(db.read_bytes()).hexdigest()==hash_before
    with sqlite3.connect(tmp_path/'small.sqlite') as conn:
        assert raw_store_health(conn)=={'shared_rows':120,'references':240,'dangling_refs':0}
        assert {row[0]:resolved_raw_row(conn,row[0]) for row in conn.execute('SELECT id FROM v2_samples')}==original_raw
        assert [r[0] for r in conn.execute('SELECT label_int FROM v2_samples ORDER BY id')]==original_labels
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    with pytest.raises(FileExistsError):compact(db,tmp_path/'small.sqlite')


def test_corrupt_old_shared_ordinals_rejected_without_output(tmp_path):
    db=blank(tmp_path);import_file(db,input_gz(tmp_path),config());_force_old_inline(db)
    with sqlite3.connect(db) as conn:
        conn.execute('''UPDATE v2_samples SET raw_row_json='{"tampered":true}'
             WHERE id=(SELECT MAX(id) FROM v2_samples)''')
    with pytest.raises(ValueError,match='不一致'):
        compact(db,tmp_path/'should_not_exist.sqlite')
    assert not (tmp_path/'should_not_exist.sqlite').exists()
    assert not (tmp_path/'should_not_exist.sqlite.partial').exists()


def test_compaction_dry_run_never_creates_output(tmp_path):
    db=blank(tmp_path);import_file(db,input_gz(tmp_path),config());_force_old_inline(db)
    report=compact(db,tmp_path/'not_here.sqlite',dry_run=True)
    assert report['status']=='preview' and report['candidates']
    assert not (tmp_path/'not_here.sqlite').exists()


def test_migration_reduces_many_channel_wide_storage(tmp_path):
    db=blank(tmp_path)
    names=[f'sensor_{i}' for i in range(12)]
    values=['0.123456789123456789']*len(names)
    csv='timestamp,'+','.join(names)+',label\n'
    csv+=''.join(f'{i},'+','.join(values)+',0\n' for i in range(250))
    payload={**config(),'filename':'many_cols.csv','csv':csv,
             'column_metadata':{n:{'subsystem':'ADCS','device':'component',
                 'variable':n,'unit':'1'} for n in names}}
    import_wide_csv(db,payload,commit=True)
    _force_old_inline(db)
    old_size=db.stat().st_size
    compact(db,tmp_path/'smaller.sqlite')
    assert (tmp_path/'smaller.sqlite').stat().st_size<old_size*0.65
    with sqlite3.connect(tmp_path/'smaller.sqlite') as conn:
        assert raw_store_health(conn)=={'shared_rows':250,'references':3000,'dangling_refs':0}
        assert conn.execute('SELECT COUNT(*) FROM v2_samples WHERE label_int=0').fetchone()[0]==3000


def test_migration_closes_all_partial_sqlite_connections_before_publication(tmp_path,monkeypatch):
    """Regression for Windows WinError 32: sqlite3 `with` alone does NOT close."""
    from scripts import compact_v2_raw_rows as module
    source=blank(tmp_path)
    import_file(source,input_gz(tmp_path),config())
    _force_old_inline(source)
    real_connect=sqlite3.connect
    opened_partial=[]

    def tracked_connect(*args,**kwargs):
        kwargs.setdefault('factory',sqlite3.Connection)
        conn=real_connect(*args,**kwargs)
        if str(args[0]).endswith('.partial'):
            opened_partial.append(conn)
        return conn

    real_publish=module._publish_verified_copy

    def confirm_all_closed(partial,output):
        assert len(opened_partial)>=2
        for conn in opened_partial:
            with pytest.raises(sqlite3.ProgrammingError,match='closed'):
                conn.execute('SELECT 1')
        return real_publish(partial,output)

    monkeypatch.setattr(sqlite3,'connect',tracked_connect)
    monkeypatch.setattr(module,'_publish_verified_copy',confirm_all_closed)
    report=compact(source,tmp_path/'verified.sqlite')
    assert report['status']=='created'
    assert not (tmp_path/'verified.sqlite.partial').exists()
