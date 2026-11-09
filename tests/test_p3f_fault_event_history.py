from __future__ import annotations

import csv
import io
import sqlite3

import pytest

from scripts.server import v2_csv_workspace as ws


def _import(root, frames=6):
    lines = ['time_s,speed,temp,label']
    for frame in range(frames):
        lines.append(f'{frame},{10 + frame},{20 + frame},0')
    raw = ('\n'.join(lines) + '\n').encode()
    return ws.import_csv(root, raw, 'history.csv', csv_encoding='utf-8',
                         csv_delimiter=',', format_confirmed=True)


def _inject(root, dataset_id, *, column='speed', start=2, end=4,
            fault_type='bias', magnitude=5):
    config = {'dataset_id': dataset_id, 'column': column, 'fault_type': fault_type,
              'start_frame': start, 'end_frame': end, 'magnitude': magnitude}
    preview = ws.preview_fault(root, config)
    return ws.save_fault(root, {**config, 'preview_sha256': preview['preview_sha256']})


def test_event_timeline_defaults_legacy_events_to_active_and_reports_same_channel_overlap(tmp_path):
    original = _import(tmp_path)
    first = _inject(tmp_path, original['id'], column='speed', start=2, end=4)
    second = _inject(tmp_path, first['id'], column='speed', start=4, end=5,
                     fault_type='stuck', magnitude=99)
    third = _inject(tmp_path, second['id'], column='temp', start=4, end=5)

    timeline = ws.event_timeline(tmp_path, third['id'])
    events = timeline['events']
    assert [event['sequence'] for event in events] == [1, 2, 3]
    assert [event['status'] for event in events] == ['active', 'active', 'active']
    assert events[0]['overlaps'] == [{'event_id': events[1]['event_id'],
                                      'start_frame': 4, 'end_frame': 4, 'frame_count': 1}]
    assert events[1]['overlaps'] == [{'event_id': events[0]['event_id'],
                                      'start_frame': 4, 'end_frame': 4, 'frame_count': 1}]
    assert events[2]['overlaps'] == []


def test_retract_one_event_replays_remaining_events_from_original_and_keeps_snapshots_immutable(tmp_path):
    original = _import(tmp_path, frames=4)
    first = _inject(tmp_path, original['id'], start=2, end=3, magnitude=5)
    second = _inject(tmp_path, first['id'], start=3, end=4,
                     fault_type='stuck', magnitude=2)
    first_before = ws.get_dataset(tmp_path, first['id'])
    second_before = ws.get_dataset(tmp_path, second['id'])
    event_id = second_before['events'][0]['event_id']

    preview = ws.preview_retract_event(tmp_path, second['id'], event_id)
    assert preview['changed_frame_count'] == 1
    assert preview['active_event_count'] == 1
    result = ws.retract_event(tmp_path, {
        'dataset_id': second['id'], 'event_id': event_id,
        'preview_sha256': preview['preview_sha256'],
    })

    child = ws.get_dataset(tmp_path, result['id'])
    assert child['parent_id'] == second['id']
    assert child['version'] == 3
    assert [row['row']['speed'] for row in child['frames']] == ['10', '11', '2', '2']
    assert [event['status'] for event in child['events']] == ['retracted', 'active']
    assert child['operations'][-1]['type'] == 'event_retracted'
    assert ws.get_dataset(tmp_path, first['id']) == first_before
    assert ws.get_dataset(tmp_path, second['id']) == second_before


def test_retract_requires_matching_preview_and_never_mutates_parent(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    event_id = ws.get_dataset(tmp_path, child['id'], include_frames=False)['events'][0]['event_id']
    before = len(ws.list_datasets(tmp_path)['items'])
    with pytest.raises(ValueError, match='预览'):
        ws.retract_event(tmp_path, {'dataset_id': child['id'], 'event_id': event_id,
                                    'preview_sha256': '0' * 64})
    assert len(ws.list_datasets(tmp_path)['items']) == before
    assert ws.get_dataset(tmp_path, child['id'], include_frames=False)['events'][0]['status'] == 'active'


def test_rollback_to_ancestor_creates_new_child_and_retracts_descendant_events(tmp_path):
    original = _import(tmp_path, frames=4)
    first = _inject(tmp_path, original['id'], start=2, end=3, magnitude=5)
    second = _inject(tmp_path, first['id'], start=3, end=4,
                     fault_type='stuck', magnitude=2)
    preview = ws.preview_rollback(tmp_path, second['id'], first['id'])
    result = ws.rollback_to_ancestor(tmp_path, {
        'dataset_id': second['id'], 'ancestor_id': first['id'],
        'preview_sha256': preview['preview_sha256'],
    })
    rolled = ws.get_dataset(tmp_path, result['id'])
    assert rolled['parent_id'] == second['id']
    assert rolled['version'] == 3
    assert [row['row']['speed'] for row in rolled['frames']] == ['10', '16', '17', '13']
    assert [event['status'] for event in rolled['events']] == ['active', 'retracted']
    assert rolled['operations'][-1]['type'] == 'version_rollback'
    assert ws.get_dataset(tmp_path, second['id'])['frames'][2]['row']['speed'] == '2'


def test_rollback_rejects_sibling_or_unrelated_version(tmp_path):
    original = _import(tmp_path)
    left = _inject(tmp_path, original['id'], start=1, end=2, magnitude=3)
    right = _inject(tmp_path, original['id'], start=4, end=5, magnitude=8)
    with pytest.raises(ValueError, match='祖先'):
        ws.preview_rollback(tmp_path, left['id'], right['id'])


def test_compare_versions_counts_all_changed_frames_cells_and_event_deltas(tmp_path):
    original = _import(tmp_path, frames=6)
    first = _inject(tmp_path, original['id'], column='speed', start=2, end=3, magnitude=5)
    second = _inject(tmp_path, first['id'], column='temp', start=4, end=5,
                     fault_type='stuck', magnitude=99)
    result = ws.compare_versions(tmp_path, original['id'], second['id'], column='speed')
    assert result['changed_frame_count'] == 4
    assert result['changed_cell_count'] == 4
    assert result['changed_by_column'] == {'speed': 2, 'temp': 2}
    assert result['changed_ranges'] == [{'start_frame': 2, 'end_frame': 3, 'frame_count': 2}]
    assert len(result['points']) == 6
    assert result['events_added'] == [event['event_id'] for event in ws.get_dataset(tmp_path, second['id'], include_frames=False)['events']]


def test_compare_versions_rejects_different_import_roots(tmp_path):
    one = _import(tmp_path)
    two = _import(tmp_path)
    with pytest.raises(ValueError, match='同一原始 CSV'):
        ws.compare_versions(tmp_path, one['id'], two['id'])


def test_dataset_list_returns_event_counts_without_expanding_event_ledger(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    item = next(entry for entry in ws.list_datasets(tmp_path)['items'] if entry['id'] == child['id'])
    assert item['event_count'] == 1
    assert item['active_event_count'] == 1
    assert 'events' not in item


def test_authenticated_http_routes_cover_timeline_compare_retract_and_rollback(tmp_path):
    import base64
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from scripts import visualization_server as app
    from scripts.server.home_auth import HomeAuth

    auth = HomeAuth(tmp_path)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(app, 'ROOT', tmp_path)
        monkeypatch.setattr(app, 'HOME_AUTH', auth)
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.DashboardHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'

        def post(path, body, headers=None):
            request = Request(origin + path, data=body, headers=headers or {'Content-Type': 'application/json'})
            with urlopen(request, timeout=8) as response:
                return json.load(response), response.headers.get('Set-Cookie', '')

        def get(path, cookie):
            with urlopen(Request(origin + path, headers={'Cookie': cookie}), timeout=8) as response:
                return json.load(response)

        try:
            post('/api/auth/setup', json.dumps({'username': 'admin', 'password': 'a-strong-password-for-testing'}).encode())
            _, cookie = post('/api/auth/login', json.dumps({'username': 'admin', 'password': 'a-strong-password-for-testing'}).encode())
            cookie = cookie.split(';', 1)[0]
            raw = b'time_s,speed,temp,label\n0,10,20,0\n1,11,21,0\n2,12,22,0\n3,13,23,0\n'
            upload_headers = {
                'Cookie': cookie, 'Content-Type': 'application/octet-stream',
                'X-AAS-CSV-Filename': 'http.csv',
                'X-AAS-CSV-Format-B64': base64.b64encode(json.dumps({
                    'csv_encoding': 'utf-8', 'csv_delimiter': ',', 'format_confirmed': True,
                }).encode()).decode(),
            }
            imported, _ = post('/api/v2/csv-workspace/import', raw, upload_headers)
            first_config = {'dataset_id': imported['id'], 'column': 'speed', 'fault_type': 'bias',
                            'start_frame': 2, 'end_frame': 3, 'magnitude': 5}
            first_preview, _ = post('/api/v2/csv-workspace/fault-preview', json.dumps(first_config).encode(),
                                    {'Cookie': cookie, 'Content-Type': 'application/json'})
            first, _ = post('/api/v2/csv-workspace/fault-save', json.dumps({
                **first_config, 'preview_sha256': first_preview['preview_sha256']}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})
            second_config = {'dataset_id': first['id'], 'column': 'temp', 'fault_type': 'stuck',
                             'start_frame': 3, 'end_frame': 4, 'magnitude': 99}
            second_preview, _ = post('/api/v2/csv-workspace/fault-preview', json.dumps(second_config).encode(),
                                     {'Cookie': cookie, 'Content-Type': 'application/json'})
            second, _ = post('/api/v2/csv-workspace/fault-save', json.dumps({
                **second_config, 'preview_sha256': second_preview['preview_sha256']}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})

            timeline = get('/api/v2/csv-workspace/events?id=' + second['id'], cookie)
            assert timeline['event_count'] == 2 and timeline['active_event_count'] == 2
            ancestors = get('/api/v2/csv-workspace/ancestors?id=' + second['id'], cookie)
            assert [item['id'] for item in ancestors['ancestors']] == [first['id'], imported['id']]
            comparison = get('/api/v2/csv-workspace/compare?before_id=' + imported['id'] +
                             '&after_id=' + second['id'] + '&column=speed', cookie)
            assert comparison['changed_frame_count'] == 3
            event_id = timeline['events'][0]['event_id']
            retract_preview, _ = post('/api/v2/csv-workspace/event-retract-preview', json.dumps({
                'dataset_id': second['id'], 'event_id': event_id}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})
            retracted, _ = post('/api/v2/csv-workspace/event-retract', json.dumps({
                'dataset_id': second['id'], 'event_id': event_id,
                'preview_sha256': retract_preview['preview_sha256']}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})
            assert retracted['version'] == 3
            updated_timeline = get('/api/v2/csv-workspace/events?id=' + retracted['id'], cookie)
            assert updated_timeline['active_event_count'] == 1

            rollback_preview, _ = post('/api/v2/csv-workspace/rollback-preview', json.dumps({
                'dataset_id': retracted['id'], 'ancestor_id': first['id']}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})
            assert rollback_preview['events_retracted'] == 1
            assert rollback_preview['events_reactivated'] == 1
            rolled, _ = post('/api/v2/csv-workspace/rollback', json.dumps({
                'dataset_id': retracted['id'], 'ancestor_id': first['id'],
                'preview_sha256': rollback_preview['preview_sha256']}).encode(),
                {'Cookie': cookie, 'Content-Type': 'application/json'})
            assert rolled['version'] == 4
            assert rolled['parent_id'] == retracted['id']
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_p3f_browser_history_contract():
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('本环境没有 Node；跳过浏览器脚本契约测试')
    result = subprocess.run(['node', 'tests/p3f_fault_history_ui.cjs'],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


def test_legacy_event_ledger_without_status_remains_active_and_operations_column_migrates(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    db = tmp_path / 'datasets' / 'aas_simulation_v2.sqlite'
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(v2_csv_datasets)')}
        assert 'operations_json' in columns
        row = conn.execute('SELECT events_json FROM v2_csv_datasets WHERE id=?', (child['id'],)).fetchone()
        legacy = [{key: value for key, value in event.items()
                   if key not in ('status', 'sequence', 'revision')}
                  for event in __import__('json').loads(row[0])]
        conn.execute('UPDATE v2_csv_datasets SET events_json=? WHERE id=?',
                     (__import__('json').dumps(legacy), child['id']))
    timeline = ws.event_timeline(tmp_path, child['id'])
    assert timeline['events'][0]['status'] == 'active'
    assert timeline['events'][0]['sequence'] == 1


def test_p3f_50k_frame_compare_retract_and_rollback_are_bounded(tmp_path):
    lines = ['time_s,speed,temp,label']
    lines.extend(f'{frame},{10 + frame / 10},{20 + frame / 20},0' for frame in range(50_000))
    raw = ('\n'.join(lines) + '\n').encode()
    original = ws.import_csv(tmp_path, raw, '50k.csv', csv_encoding='utf-8',
                             csv_delimiter=',', format_confirmed=True)
    first = _inject(tmp_path, original['id'], column='speed', start=100, end=200, magnitude=5)
    second = _inject(tmp_path, first['id'], column='temp', start=200, end=300,
                     fault_type='stuck', magnitude=99)

    comparison = ws.compare_versions(tmp_path, original['id'], second['id'],
                                     column='speed', max_points=1000)
    assert comparison['changed_frame_count'] == 201
    assert comparison['changed_cell_count'] == 202
    assert len(comparison['points']) <= 1000
    assert comparison['plot_sampled'] is True

    event_id = ws.get_dataset(tmp_path, second['id'], include_frames=False)['events'][0]['event_id']
    preview = ws.preview_retract_event(tmp_path, second['id'], event_id)
    retracted = ws.retract_event(tmp_path, {'dataset_id': second['id'], 'event_id': event_id,
                                            'preview_sha256': preview['preview_sha256']})
    assert retracted['active_event_count'] == 1
    rollback_preview = ws.preview_rollback(tmp_path, retracted['id'], first['id'])
    rolled = ws.rollback_to_ancestor(tmp_path, {'dataset_id': retracted['id'],
        'ancestor_id': first['id'], 'preview_sha256': rollback_preview['preview_sha256']})
    assert rolled['frame_count'] == 50_000
    assert rolled['active_event_count'] == 1


def test_compare_fails_closed_when_snapshot_frame_rows_are_incomplete(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    db = tmp_path / 'datasets' / 'aas_simulation_v2.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM v2_csv_frames WHERE dataset_id=? AND frame=2', (child['id'],))
    with pytest.raises(ValueError, match='不完整比较结果'):
        ws.compare_versions(tmp_path, original['id'], child['id'])


def test_corrupt_event_status_is_rejected_instead_of_treated_as_active(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    db = tmp_path / 'datasets' / 'aas_simulation_v2.sqlite'
    with sqlite3.connect(db) as conn:
        row = conn.execute('SELECT events_json FROM v2_csv_datasets WHERE id=?', (child['id'],)).fetchone()
        events = __import__('json').loads(row[0])
        events[0]['status'] = 'maybe'
        conn.execute('UPDATE v2_csv_datasets SET events_json=? WHERE id=?',
                     (__import__('json').dumps(events), child['id']))
    with pytest.raises(ValueError, match='事件账本'):
        ws.event_timeline(tmp_path, child['id'])


def test_retracted_event_is_excluded_from_chart_overlay_and_machine_export(tmp_path):
    original = _import(tmp_path, frames=4)
    child = _inject(tmp_path, original['id'], start=2, end=3)
    event_id = ws.get_dataset(tmp_path, child['id'], include_frames=False)['events'][0]['event_id']
    preview = ws.preview_retract_event(tmp_path, child['id'], event_id)
    retracted = ws.retract_event(tmp_path, {'dataset_id': child['id'], 'event_id': event_id,
                                            'preview_sha256': preview['preview_sha256']})
    chart = ws.dataset_preview(tmp_path, retracted['id'], 'speed')
    assert chart['events'] == []
    assert not any(point['injected'] for point in chart['points'])
    exported = list(csv.DictReader(io.StringIO(
        ws.export_csv(tmp_path, retracted['id'], spreadsheet_safe=False).decode('utf-8-sig'))))
    assert [row['aas_injected_fault'] for row in exported] == ['0', '0', '0', '0']
    assert all(row['aas_fault_events'] == '' for row in exported)


def test_event_replay_fails_closed_when_original_snapshot_has_missing_frames(tmp_path):
    original = _import(tmp_path)
    child = _inject(tmp_path, original['id'])
    event_id = ws.get_dataset(tmp_path, child['id'], include_frames=False)['events'][0]['event_id']
    db = tmp_path / 'datasets' / 'aas_simulation_v2.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM v2_csv_frames WHERE dataset_id=? AND frame=2', (original['id'],))
    with pytest.raises(ValueError, match='原始 CSV 快照帧记录不完整'):
        ws.preview_retract_event(tmp_path, child['id'], event_id)


def test_timeline_caps_overlap_examples_but_reports_exact_count(tmp_path):
    current = _import(tmp_path)
    for _ in range(25):
        current = _inject(tmp_path, current['id'], start=2, end=4, magnitude=1)
    timeline = ws.event_timeline(tmp_path, current['id'])
    assert all(event['overlap_count'] == 24 for event in timeline['events'])
    assert all(len(event['overlaps']) == 20 for event in timeline['events'])
    assert all(event['overlaps_truncated'] is True for event in timeline['events'])
