"""Event-level detection metrics, boundaries and independent archive evidence."""
import json
from pathlib import Path
import sqlite3
import tempfile

from scripts.core.fault_intervals import create_interval
from scripts.server.event_scoring import enrich_trace, load_events, source_tables, summarize_events
from scripts.server.result_traces import chart_trace


def _event(start, end, label, table='temperature', kind='numeric'):
    return {'source_table': table, 'start_timestamp': str(start), 'end_timestamp': str(end),
            'timestamp_kind': kind, 'label': label, 'fault_type': 'anomaly' if label else ''}


def _result(n=12, alerts=(4, 5, 9)):
    return {'rows': [{'timestamp': float(i), 'label': 0} for i in range(n)],
            'threshold': [float(i) for i in range(n)], 'decision_threshold': 4.,
            'pred_labels': [int(i in alerts) for i in range(n)]}


def test_event_metrics_fully_annotated_and_unknown_not_normal():
    events = [_event(3, 7, 1), _event(8, 10, 1), _event(0, 2, 0)]
    metrics = summarize_events(_result(), events)
    assert metrics['fault_events_evaluable'] == 2
    assert metrics['fault_events_detected'] == 2
    assert metrics['event_recall'] == 1.0
    assert metrics['median_first_alarm_delay_seconds'] == 1.0
    assert metrics['confirmed_normal_samples'] == 2
    assert metrics['confirmed_normal_false_alarms'] == 0
    assert metrics['confirmed_normal_false_alarm_rate'] == 0.0


def test_end_exclusive_missing_and_left_censored_fault():
    data = _result(alerts=(7,))
    metrics = summarize_events(data, [_event(4, 7, 1), _event(-3, 1, 1), _event(20, 25, 1)])
    assert metrics['fault_events_total'] == 3
    assert metrics['fault_events_evaluable'] == 1
    assert metrics['fault_events_detected'] == 0
    assert metrics['fault_events_not_evaluable'] == 2
    assert metrics['event_recall'] == 0.0
    assert metrics['median_first_alarm_delay_seconds'] is None


def test_without_prediction_or_invalid_timestamps_is_unavailable():
    result = _result()
    result.pop('pred_labels')
    assert summarize_events(result, [_event(2, 5, 1)])['status'] == 'unavailable'
    result['pred_labels'] = [0]*12
    result['rows'][3]['timestamp'] = 'bad'
    assert summarize_events(result, [_event(2, 5, 1)])['status'] == 'unavailable'
    assert summarize_events(_result(), [])['event_recall'] is None


def test_source_order_and_independent_multi_table_events():
    config = {'sourceTables': ['temperature', 'current', 'temperature'], 'table':'temperature'}
    assert source_tables(config) == ['temperature', 'current']
    assert source_tables({'table':'temp'}) == ['temp']
    assert source_tables({'tables': list(map(str,range(17)))}) == []
    metrics = summarize_events(_result(), [_event(3,7,1,'temperature'), _event(3,7,1,'current')])
    assert metrics['fault_events_evaluable'] == 2
    assert metrics['fault_events_detected'] == 2


def test_load_readonly_and_snapshot_is_immutable():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'test.db'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE temperature(timestamp REAL, value REAL, label INTEGER)')
            conn.executemany('INSERT INTO temperature VALUES (?,?,?)', [(float(i), i * 2, 0) for i in range(12)])
            create_interval(conn, table='temperature', start=3, end=7, label=1, fault_type='rise')
            create_interval(conn, table='temperature', start=0, end=2, label=0)
        result = _result()
        events = load_events(path, ['temperature'])
        assert len(events) == 2
        trace = enrich_trace(chart_trace(result), result, events, ['temperature'])
        assert trace['event_overlay']['status'] == 'ok'
        assert trace['event_metrics']['fault_events_evaluable'] == 1
        json.dumps(trace, allow_nan=False)
        with sqlite3.connect(path) as conn:
            conn.execute('DELETE FROM aas_fault_intervals')
        assert len(trace['event_overlay']['items']) == 2  # frozen history
        assert load_events(path, ['temperature']) == []


def test_metrics_on_full_series_not_decimated_curve():
    result = _result(n=6500, alerts=(3121,))
    result['rows'] = [{'timestamp': i} for i in range(6500)]
    trace = enrich_trace(chart_trace(result), result, [_event(3120,3125,1)], ['temperature'])
    assert trace['sampled']
    assert trace['event_metrics']['event_recall'] == 1
    assert trace['event_metrics']['median_first_alarm_delay_seconds'] == 1


def test_authenticated_http_archive_freezes_event_annotations():
    """Actual HTTP save/read uses SQLite events, not user-supplied event claims."""
    from http.server import ThreadingHTTPServer
    import threading
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from unittest.mock import patch

    from scripts import visualization_server as app
    from scripts.server.home_auth import HomeAuth

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / 'datasets').mkdir()
        (root / 'configs' / 'experiments').mkdir(parents=True)
        db = root / 'datasets' / 'sample.db'
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE temperature(timestamp REAL,value REAL,label INTEGER)')
            conn.executemany('INSERT INTO temperature VALUES (?,?,?)', [(i, float(i), 0) for i in range(8)])
            create_interval(conn, table='temperature', start=2, end=5, label=1)
            create_interval(conn, table='temperature', start=0, end=2, label=0)
        auth = HomeAuth(root)
        with patch.object(app, 'ROOT', root), patch.object(app, 'HOME_AUTH', auth):
            srv = ThreadingHTTPServer(('127.0.0.1', 0), app.DashboardHandler)
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f'http://127.0.0.1:{srv.server_port}'

                def request(path, body=None, cookie=''):
                    headers = {'Content-Type': 'application/json'}
                    if cookie:
                        headers['Cookie'] = cookie
                    req = Request(origin + path,
                                  data=json.dumps(body).encode() if body is not None else None,
                                  headers=headers)
                    with urlopen(req, timeout=5) as res:
                        return json.load(res), res.headers.get('Set-Cookie', '')

                request('/api/auth/setup', {'username':'admin','password':'long-password-for-testing'})
                _, cookie = request('/api/auth/login',
                                    {'username':'admin','password':'long-password-for-testing'})
                cookie = cookie.split(';',1)[0]
                rows = [{'timestamp': i, 'label': int(i in (2,3,4))} for i in range(8)]
                payload = {
                    'type': 'evaluation', 'model_id': 1,
                    'config': {'dataset': 'datasets/sample.db', 'table':'temperature'},
                    'result': {'rows': rows, 'threshold': [0.1]*8, 'decision_threshold':1,
                               'pred_labels': [0,0,0,1,0,0,0,0],
                               'event_overlay': {'items': [{'label': 1, 'start_timestamp': 0}]}}
                }
                receipt = app.EXECUTION_REGISTRY.issue(
                    kind='evaluation', config=payload['config'], result=payload['result'],
                    identity={'model_id':1}, root=root, db_path=db)
                saved, _ = request('/api/save-experiment',
                    {'type':'evaluation','execution_receipt':receipt},cookie)
                path = f'outputs/experiment_{saved["experiment_id"]}/reports/summary.json'
                detail, _ = request('/api/experiment-details?' + urlencode({'path':path}),cookie=cookie)
                trace = detail['rows'][0]['chart_trace']
                assert trace['event_overlay']['total_intervals'] == 2
                assert trace['event_metrics']['fault_events_detected'] == 1
                assert trace['event_metrics']['median_first_alarm_delay_seconds'] == 1.0
                assert trace['event_metrics']['confirmed_normal_samples'] == 2
                # An editor can change current intervals, but the archived
                # experiment retains the annotations it was evaluated against.
                with sqlite3.connect(db) as conn:
                    conn.execute('DELETE FROM aas_fault_intervals')
                again, _ = request('/api/experiment-details?' + urlencode({'path':path}),cookie=cookie)
                assert again['rows'][0]['chart_trace'] == trace
            finally:
                srv.shutdown()
                thread.join(timeout=5)
                srv.server_close()


def test_joint_normal_labels_count_each_timestamp_once():
    """Two confirmed-normal sensors do not double the evaluation denominator."""
    predictions = _result(n=10, alerts=(1, 9))
    events = [_event(0, 10, 0, 'temperature'), _event(0, 10, 0, 'current')]
    metrics = summarize_events(predictions, events, ['temperature', 'current'])
    assert metrics['confirmed_normal_samples'] == 10
    assert metrics['confirmed_normal_false_alarms'] == 2
    assert metrics['confirmed_normal_false_alarm_rate'] == .2
    assert metrics['joint_unknown_samples'] == 0


def test_joint_normal_excludes_other_source_faults_and_unknown_time():
    """An algorithm alert while another source is faulty is not a normal FP."""
    predictions = _result(n=10, alerts=(1, 4, 5, 8))
    events = [
        _event(0, 10, 0, 'temperature'),
        _event(0, 3, 0, 'current'),
        _event(5, 7, 1, 'current'),
    ]
    metrics = summarize_events(predictions, events, ['temperature', 'current'])
    assert metrics['confirmed_normal_samples'] == 3
    assert metrics['confirmed_normal_false_alarms'] == 1
    assert metrics['joint_fault_samples'] == 2
    assert metrics['joint_unknown_samples'] == 5
    assert metrics['fault_events_detected'] == 1


def test_missing_second_source_annotation_is_unknown_not_normal():
    metrics = summarize_events(_result(n=10, alerts=(2,)),
                               [_event(0, 10, 0, 'temperature')],
                               ['temperature', 'vibration'])
    assert metrics['confirmed_normal_samples'] == 0
    assert metrics['confirmed_normal_false_alarms'] == 0
    assert metrics['confirmed_normal_false_alarm_rate'] is None
    assert metrics['joint_unknown_samples'] == 10


def test_overlapping_normal_spans_same_source_not_double_counted():
    metrics = summarize_events(_result(n=10, alerts=(1,)),
                               [_event(0, 7, 0), _event(3, 10, 0)])
    assert metrics['confirmed_normal_samples'] == 10
    assert metrics['confirmed_normal_false_alarms'] == 1


def test_incomplete_right_censored_fault_not_counted_as_miss():
    metrics = summarize_events(_result(n=10, alerts=()),
                               [_event(7, 14, 1)])
    assert metrics['fault_events_total'] == 1
    assert metrics['fault_events_right_censored'] == 1
    assert metrics['fault_events_evaluable'] == 0
    assert metrics['fault_events_not_evaluable'] == 1
    assert metrics['event_recall'] is None


def test_archived_chart_uses_detector_labels_without_bool_coercion():
    data = _result(n=10, alerts=())
    data['pred_labels'] = ['0'] * 9 + ['1']
    trace = chart_trace(data)
    assert trace['pred_labels'] == [0] * 9 + [1]
    data['pred_labels'][2] = 'unexpected'
    assert chart_trace(data)['pred_labels'] is None


def test_joint_archive_uses_declared_sources_and_full_detector_predictions():
    """The archived report must use the full, non-decimated output semantics."""
    result = _result(n=10, alerts=(1, 5))
    events = [_event(0, 10, 0, 'temperature'), _event(5, 7, 1, 'current')]
    trace = enrich_trace(chart_trace(result, limit=5), result, events,
                         ['temperature', 'current'])
    metrics = trace['event_metrics']
    assert metrics['confirmed_normal_samples'] == 0  # current has no normal interval
    assert metrics['joint_fault_samples'] == 2
    assert metrics['joint_unknown_samples'] == 8
    assert metrics['fault_events_evaluable'] == 1
    assert metrics['fault_events_detected'] == 1
    assert trace['event_overlay']['sources'] == ['temperature', 'current']
