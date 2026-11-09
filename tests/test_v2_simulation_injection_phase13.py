"""Phase 13: sparse injection never makes a full source-database clone."""
from pathlib import Path
import sqlite3

import pytest
from scripts.server.v2_simulation_injection import (
    SIM_NAME,FAULT_COLUMNS,inject_from_gzip,preview_injection,seed_default,list_injections
)
from scripts.server.telemetry_v2_access import list_channels,query_charts

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def sandbox(tmp_path):
    import shutil
    (tmp_path/'datasets/source_csv_gz').mkdir(parents=True)
    (tmp_path/'datasets/source_labels').mkdir()
    original=ROOT/'datasets/source_csv_gz'
    if not original.is_dir():
        pytest.skip("Phase 15 之后不再附带原始 CSV.gz；使用 Phase 16 V2 原生模拟测试")
    shutil.copy2(ROOT/'datasets/source_labels/targets.zip',tmp_path/'datasets/source_labels/targets.zip')
    for src in original.glob('*.csv.gz'):
        # One normal training source verified against the actual provided targets.
        if src.name.startswith('0a31d3cf5cb6b7ea3a8d3d71'):
            shutil.copy2(src,tmp_path/'datasets/source_csv_gz'/src.name)
    return tmp_path


def test_seed_sparse_injection_and_labels(sandbox):
    result=seed_default(sandbox)
    assert result['source_database_copied'] is False
    assert result['sampling_rows']==600 and result['fault_rows']==400
    path=sandbox/'datasets'/SIM_NAME
    assert path.stat().st_size < 5_000_000
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert c.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==1800
        assert c.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM v2_injection_records').fetchone()[0]==1
        assert c.execute('SELECT COUNT(DISTINCT source_table) FROM v2_segments').fetchone()[0]==3
    assert seed_default(sandbox)['status']=='exists'
    fetched=preview_injection(sandbox,1)['points']
    assert sum(row['label'] for row in fetched)==400
    assert fetched[199]['label']==0 and fetched[200]['label']==1
    channels=list_channels(path,role='test')['items']
    assert len(channels)==3
    charts=query_charts(path,role='test',channel_ids=[channels[0]['id']],limit=12)
    assert 0 < len(charts['series'][0]['points']) <= 12


def test_fails_closed_for_bad_source_and_does_not_create_database(sandbox):
    with pytest.raises(ValueError):
        inject_from_gzip(sandbox,token='1234567890abcdef12345678')
    assert not (sandbox/'datasets'/SIM_NAME).exists()


def test_injections_supported_and_legacy_copy_disabled():
    import inspect
    from scripts.visualization_server import init_simulation_db,sync_simulation_db
    assert 'shutil.copy2' not in inspect.getsource(init_simulation_db)
    assert 'retired' == sync_simulation_db()['status']
    page=(ROOT/'web/visualization/simulation.html').read_text(encoding='utf-8')
    assert 'id="simPreview"' in page
    code=(ROOT/'web/visualization/simulation-v2.js').read_text(encoding='utf-8')
    assert '/api/v2/simulation/dry-run' in code and '/api/v2/simulation/save' in code


def test_two_distinct_injections_same_source_do_not_collide(sandbox):
    seeded=seed_default(sandbox)
    second=inject_from_gzip(sandbox,token=seeded['source_token'],
        onset_s=400,magnitude=22,max_points=600,model='DEMO-RW-BIAS')
    assert second['source_id']!=seeded['source_id']
    with sqlite3.connect(sandbox/'datasets'/SIM_NAME) as conn:
        assert conn.execute('SELECT COUNT(*) FROM v2_sources').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM v2_samples').fetchone()[0]==3600
        assert conn.execute('SELECT COUNT(*) FROM v2_injection_records').fetchone()[0]==2
