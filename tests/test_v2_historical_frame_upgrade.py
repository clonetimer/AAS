"""Frame-indexed historical injection and bridging to the versioned CSV workspace."""
import hashlib
from pathlib import Path

import pytest

from tests.test_v2_simulation_hierarchical_phase16 import source_root, params
from scripts.server.v2_simulation_injection import (
    v2_source_catalog, preview_v2_injection, save_v2_injection,
    export_injection_csv, list_injections, preview_injection,
)
from scripts.server import v2_csv_workspace as ws


def _frame_config(root, **extra):
    config = params(v2_source_catalog(root))
    del config['onset_s']
    config.update(start_frame=2, end_frame=4)
    config.update(extra)
    return config


def test_historical_injection_respects_inclusive_frame_range(source_root):
    root, db, _ = source_root
    unchanged = hashlib.sha256(db.read_bytes()).hexdigest()
    config = _frame_config(root, start_frame=1, end_frame=3)
    preview = preview_v2_injection(root, config)
    assert (preview['start_frame'], preview['end_frame'], preview['fault_rows']) == (1,3,3)
    assert [point['label'] for point in preview['points'][:5]] == [1,1,1,0,0]
    assert preview['points'][2]['value'] != preview['points'][2]['original']
    assert preview['points'][3]['value'] == preview['points'][3]['original']
    assert [p['frame'] for p in preview['points'][:4]] == [1,2,3,4]
    saved = save_v2_injection(root, {**config,'preview_sha256':preview['preview_sha256']})
    rec = list_injections(root)['items'][0]
    assert saved['id'] == rec['id']
    assert (rec['start_frame'],rec['end_frame']) == (1,3)
    saved_preview = preview_injection(root, saved['id'])
    assert [p['frame'] for p in saved_preview['points'][:4]] == [1,2,3,4]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == unchanged


@pytest.mark.parametrize('frames',[(0,5),(1,601),(10,2),(1,1.5),(True,3),(1,None)])
def test_frame_bounds_rejected(source_root,frames):
    root,_,_=source_root
    with pytest.raises(ValueError,match='帧'):
        preview_v2_injection(root,_frame_config(root,start_frame=frames[0],end_frame=frames[1]))


def test_historical_saved_export_can_continue_simulation_as_immutable_csv(source_root):
    root,_,_=source_root
    cfg=_frame_config(root,start_frame=10,end_frame=20)
    p=preview_v2_injection(root,cfg)
    saved=save_v2_injection(root,{**cfg,'preview_sha256':p['preview_sha256']})
    raw=export_injection_csv(root,saved['id'],spreadsheet_safe=False)
    checked=ws.inspect_csv(raw,'history-injection-1.csv')
    assert checked['numeric_columns']==['value']
    assert checked['frame_count']==600 and checked['has_truth_labels']
    original=ws.import_csv(root,raw,'history-injection-1.csv',
                           csv_encoding='utf-8',csv_delimiter=',',format_confirmed=True)
    change={'dataset_id':original['id'],'column':'value','fault_type':'stuck',
            'start_frame':30,'end_frame':34,'magnitude':200}
    draft=ws.preview_fault(root,change)
    derived=ws.save_fault(root,{**change,'preview_sha256':draft['preview_sha256']})
    assert derived['version']==1 and derived['event_count']==1
    from scripts.server.v2_csv_workspace import get_dataset
    base=get_dataset(root,original['id'])
    final=get_dataset(root,derived['id'])
    assert float(base['frames'][29]['row']['value'])==p['points'][29]['value']
    assert float(final['frames'][29]['row']['value'])!=float(base['frames'][29]['row']['value'])
    assert final['events'][0]['start_frame']==30
    assert len(list_injections(root)['items'])==1


def test_historical_page_exposes_frame_controls_and_continue_action():
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/visualization/simulation.html').read_text('utf8')
    js=(root/'web/visualization/simulation-v2.js').read_text('utf8')
    assert 'id="simStartFrame"' in html and 'id="simEndFrame"' in html
    assert '继续注入' in js and '/api/v2/csv-workspace/import' in js
    assert 'AASCsvWorkspace.refreshDatasets' in js
