"""Phase 53: production promotion must never be inferred from a green unit suite."""
import json
import sqlite3

import pytest

from scripts.phase53_release_acceptance import audit, business_smoke, check_gate
from scripts.verify_v2_compaction import verify
from scripts.compact_v2_raw_rows import compact
from scripts.server.telemetry_v2_batch_import import import_file
from tests.test_phase51_raw_row_storage import blank, config, input_gz, _force_old_inline


def migration(tmp_path):
    original=blank(tmp_path)
    import_file(original,input_gz(tmp_path),config())
    _force_old_inline(original)
    dest=tmp_path/'migrated.sqlite'
    compact(original,dest)
    return original,dest


def gate_fixture(tmp_path):
    manifest=json.loads((__import__('pathlib').Path(__file__).parent/'v2_stable_gate.json').read_text(encoding='utf-8'))
    nodes=manifest['stable_nodes']
    report={'status':'passed','stable_node_count':len(nodes),
            'external_node_count':0,
            'batches':[{'nodes':nodes[:len(nodes)//2], 'status':'passed','exit_code':0},
                       {'nodes':nodes[len(nodes)//2:], 'status':'passed','exit_code':0}]}
    path=tmp_path/'fixture_gate.json'
    path.write_text(json.dumps(report))
    return path


def test_user_business_catalog_and_chart_identical(tmp_path):
    old,new=migration(tmp_path)
    result=business_smoke(old,new)
    assert result['status']=='verified'
    assert any(item['queries'] for item in result['roles'])
    assert any(q['outcome']=='matched' for item in result['roles'] for q in item['queries'])


def test_release_evidence_stays_candidate_without_production_and_windows(tmp_path):
    old,new=migration(tmp_path)
    report=audit(old,new,gate_fixture(tmp_path))
    assert report['fixed_v2_gate']['status']=='verified'
    assert report['full_compaction_verification']['sample_rows']==240
    assert report['release_decision'].startswith('Candidate')
    assert any('业务库' in reason for reason in report['stable_promotion_blockers'])


def test_claim_of_production_alone_never_promotes_stable(tmp_path):
    old,new=migration(tmp_path)
    report=audit(old,new,gate_fixture(tmp_path),production=True)
    assert report['release_decision']=='Candidate (not Stable)'
    assert any('签收' in item for item in report['stable_promotion_blockers'])


def test_gate_failure_and_omitted_nodes_are_rejected(tmp_path):
    path=gate_fixture(tmp_path)
    payload=json.loads(path.read_text(encoding='utf-8'))
    payload['batches'][0]['nodes'].pop()
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='门禁'):
        check_gate(path)
    payload['batches'][0]['nodes'].insert(0,'tests/made_up.py::fake')
    payload['batches'][1]['exit_code']=124
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='门禁'):
        check_gate(path)


def test_no_false_acceptance_when_values_modified(tmp_path):
    old,new=migration(tmp_path)
    with sqlite3.connect(new) as conn:
        conn.execute('UPDATE v2_samples SET value_num=value_num+1 WHERE id=(SELECT MIN(id) FROM v2_samples)')
    with pytest.raises(ValueError,match='v2_samples'):
        audit(old,new,gate_fixture(tmp_path))


def test_concurrent_write_during_snapshot_is_detected(tmp_path,monkeypatch):
    old,new=migration(tmp_path)
    with sqlite3.connect(old) as conn:
        assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    import scripts.verify_v2_compaction as module
    original=module._compare_rows
    fired=[]
    def interleaved(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[-1]=='v2_samples' and not fired:
            with sqlite3.connect(old) as writer:
                writer.execute('UPDATE v2_samples SET value_num=value_num+3 WHERE id=(SELECT MIN(id) FROM v2_samples)')
            fired.append(True)
        return result
    monkeypatch.setattr(module,'_compare_rows',interleaved)
    with pytest.raises(ValueError,match='并发写入'):
        verify(old,new)
    assert fired


def test_late_write_after_semantic_verification_invalidates_release(tmp_path,monkeypatch):
    old,new=migration(tmp_path)
    import scripts.phase53_release_acceptance as module
    original=module.business_smoke
    def interleaved(a,b):
        matched=original(a,b)
        with sqlite3.connect(new) as conn:
            conn.execute('UPDATE v2_samples SET value_num=value_num+1 WHERE id=(SELECT MIN(id) FROM v2_samples)')
        return matched
    monkeypatch.setattr(module,'business_smoke',interleaved)
    with pytest.raises(ValueError,match='并发数据库写入'):
        audit(old,new,gate_fixture(tmp_path))
