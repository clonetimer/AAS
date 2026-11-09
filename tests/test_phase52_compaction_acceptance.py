"""Phase 52: no damaged copy is ever published as an accepted migration."""
from pathlib import Path
import sqlite3
import pytest

from scripts.compact_v2_raw_rows import compact
from scripts.verify_v2_compaction import verify
from tests.test_phase51_raw_row_storage import blank, config, input_gz, _force_old_inline
from scripts.server.telemetry_v2_batch_import import import_file


def fixture_copy(tmp_path):
    original = blank(tmp_path)
    import_file(original, input_gz(tmp_path), config())
    _force_old_inline(original)
    published = tmp_path/'compact.sqlite'
    result=compact(original, published)
    assert result['verification']['sample_rows']==240
    return original,published


def test_full_acceptance_passes_before_publication(tmp_path):
    source,dest=fixture_copy(tmp_path)
    outcome=verify(source,dest)
    assert outcome['status']=='verified'
    assert outcome['sample_rows']==240
    assert outcome['sample_content_sha256']
    assert outcome['new_raw_storage']['dangling_refs']==0
    assert dest.stat().st_size < source.stat().st_size


@pytest.mark.parametrize('column,mutation',[
    ('value_num','value_num=value_num+1'),
    ('label_int','label_int=1'),
    ('timestamp_raw',"timestamp_raw='99999'"),
])
def test_mutated_sample_cannot_pass_verification(tmp_path,column,mutation):
    source,dest=fixture_copy(tmp_path)
    with sqlite3.connect(dest) as db:
        db.execute(f'UPDATE v2_samples SET {mutation} WHERE id=(SELECT MIN(id) FROM v2_samples)')
    with pytest.raises(ValueError, match='v2_samples'):
        verify(source,dest)


def test_mutated_shared_raw_row_is_rejected(tmp_path):
    source,dest=fixture_copy(tmp_path)
    with sqlite3.connect(dest) as db:
        db.execute("UPDATE v2_source_raw_rows SET raw_row_json='{}' WHERE source_ordinal=0")
    with pytest.raises(ValueError, match='v2_samples'):
        verify(source,dest)


def test_deleted_shared_row_rejected_even_when_sqlite_integrity_ok(tmp_path):
    source,dest=fixture_copy(tmp_path)
    with sqlite3.connect(dest) as db:
        db.execute('DELETE FROM v2_source_raw_rows WHERE source_ordinal=0')
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    with pytest.raises(ValueError, match='悬空'):
        verify(source,dest)


def test_mutated_metadata_rejected(tmp_path):
    source,dest=fixture_copy(tmp_path)
    with sqlite3.connect(dest) as db:
        db.execute("UPDATE v2_sources SET source_name='changed' WHERE id=(SELECT MIN(id) FROM v2_sources)")
    with pytest.raises(ValueError, match='v2_sources'):
        verify(source,dest)


def test_verification_failure_prevents_publishing_destination(tmp_path,monkeypatch):
    from scripts import compact_v2_raw_rows as module
    source=blank(tmp_path)
    import_file(source,input_gz(tmp_path),config())
    _force_old_inline(source)
    published=tmp_path/'rejected.sqlite'
    def injected_failure(*_):
        raise ValueError('injected verification mismatch')
    monkeypatch.setattr(module,'verify',injected_failure)
    with pytest.raises(ValueError,match='injected'):
        compact(source,published)
    assert not published.exists()
    assert not Path(str(published)+'.partial').exists()
    assert source.exists()


def test_migration_reads_committed_wal_snapshot(tmp_path):
    source=blank(tmp_path)
    import_file(source,input_gz(tmp_path),config())
    _force_old_inline(source)
    with sqlite3.connect(source) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        writer.execute('UPDATE v2_samples SET label_int=0 WHERE id=(SELECT MIN(id) FROM v2_samples)')
        writer.commit()
        # A live WAL database must include committed changes in backup.
        out=compact(source,tmp_path/'wal_copy.sqlite')
        assert out['verification']['sample_rows']==240
        with sqlite3.connect(tmp_path/'wal_copy.sqlite') as copy:
            assert copy.execute('SELECT label_int FROM v2_samples ORDER BY id LIMIT 1').fetchone()[0]==0
    assert verify(source,tmp_path/'wal_copy.sqlite')['status']=='verified'
