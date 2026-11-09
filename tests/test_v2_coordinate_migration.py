import sqlite3

from scripts.migrate_v2_coordinates import migrate
from scripts.rebuild_telemetry_v2 import CANON_SCHEMA, create_v2
from scripts.server.telemetry_v2_access import query_frame_samples


def old_schema():
    schema = CANON_SCHEMA
    for line in schema.splitlines(keepends=True):
        if any(token in line for token in ('coordinate_mode TEXT', 'frame_sequence_id TEXT',
                                           'frame_index INTEGER CHECK', 'relative_seconds REAL', 'quality_flag TEXT',
                                           'idx_v2_segments_coordinate', 'idx_v2_sample_frame')):
            schema = schema.replace(line, '')
    return schema


def test_migration_keeps_legacy_samples_and_is_idempotent(tmp_path):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript(old_schema())
        conn.execute("INSERT INTO v2_meta VALUES('schema_version','2')")
        conn.execute("INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count) VALUES(1,'old','old.db','abc',1,'2026-01-01',1,1)")
        conn.execute("INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable) VALUES(1,'old','m','s','d','x')")
        conn.execute("""INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,role_provenance,time_domain,usable_for_timeseries,columns_json,source_row_count)
                        VALUES(1,1,'x',1,'telemetry','train','legacy','numeric_unspecified',1,'[]',1)""")
        conn.execute("""INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,timestamp_raw,timestamp_type,time_domain,time_sort_num,value_num,value_raw_json,label_int,label_raw_json,raw_row_json)
                        VALUES(1,1,7,'12.5','numeric','numeric_unspecified',12.5,3.2,'3.2',0,'0','{}')""")
    dry = migrate(path)
    assert not dry['applied']
    result = migrate(path, apply=True)
    assert result['before'] == result['after'] == dry['before']
    assert migrate(path, apply=True)['before'] == result['before']
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT timestamp_raw,label_int,source_ordinal,frame_index,relative_seconds FROM v2_samples').fetchone() == ('12.5', 0, 7, None, None)
        assert conn.execute('SELECT coordinate_mode,frame_sequence_id FROM v2_segments').fetchone() == ('time', None)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_frame_constraints_and_new_schema():
    with sqlite3.connect(':memory:') as conn:
        create_v2(conn)
        cols = {row[1] for row in conn.execute('PRAGMA table_info(v2_samples)')}
        assert {'frame_index', 'relative_seconds'} <= cols


def test_frame_read_keeps_sources_separate(tmp_path):
    path = tmp_path / 'frames.sqlite'
    with sqlite3.connect(path) as conn:
        create_v2(conn)
        for sid in (1, 2):
            conn.execute('''INSERT INTO v2_sources(id,namespace,source_name,source_sha256,source_bytes,imported_utc,source_table_count,source_row_count)
                            VALUES(?,?,?,?,?,?,?,?)''', (sid, f'source{sid}', 'x.csv', 'hash', 1, '2026-01-01', 1, 1))
        conn.execute("INSERT INTO v2_channels(id,namespace,model,subsystem,device,variable) VALUES(1,'n','m','s','d','x')")
        for sid in (1, 2):
            conn.execute('''INSERT INTO v2_segments(id,source_id,source_table,channel_id,kind,role,role_provenance,
                            time_domain,usable_for_timeseries,coordinate_mode,frame_sequence_id,columns_json,source_row_count)
                            VALUES(?,?,?,1,'telemetry','test','user_explicit','frame',0,'frame','same-id','[]',1)''',
                         (sid, sid, f'file{sid}'))
            conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,frame_index,time_domain,
                            value_num,value_raw_json,label_raw_json,raw_row_json)
                            VALUES(?,1,0,1,'frame',?,'1','null','{}')''', (sid, float(sid)))
        try:
            conn.execute('''INSERT INTO v2_samples(segment_id,channel_id,source_ordinal,frame_index,time_domain,
                            value_raw_json,label_raw_json,raw_row_json) VALUES(1,1,1,1,'frame','null','null','{}')''')
            assert False, 'duplicate frame must be rejected'
        except sqlite3.IntegrityError:
            pass
    result = query_frame_samples(path, source_id=1, frame_sequence_id='same-id', channel_ids=[1])
    assert [row['value_num'] for row in result['samples']] == [1.0]
    assert result['samples'][0]['timestamp_raw'] is None
