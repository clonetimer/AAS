"""Result history must show measured anomaly scores, not invented boundaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.server.result_traces import MAX_SAVED_POINTS, chart_trace
from scripts.server import services


class ResultCurveArchiveTests(unittest.TestCase):
    def test_fixed_decision_threshold_and_labels(self):
        data = {
            'algorithm': 'zscore',
            'rows': [{'timestamp': 't0', 'label': 0}, {'timestamp': 't1', 'label': 1}],
            'threshold': [0.2, 2.3],  # anomaly scores despite legacy key
            'decision_threshold': 1.5,
            'pred_labels': [0, 1],
        }
        trace = chart_trace(data)
        self.assertEqual(trace['scores'], [0.2, 2.3])
        self.assertEqual(trace['decision_threshold'], 1.5)
        self.assertIsNone(trace['decision_thresholds'])
        self.assertEqual(trace['pred_labels'], [0, 1])
        self.assertEqual(trace['rows'][1]['label'], 1)

    def test_dynamic_decision_threshold_preserved(self):
        trace = chart_trace({
            'rows': [{'timestamp': i} for i in range(3)],
            'threshold': [0.1, 0.7, 1.2],
            'decision_threshold': 0.8,
            'decision_thresholds': [0.3, 0.4, 0.5],
        })
        self.assertEqual(trace['decision_thresholds'], [0.3, 0.4, 0.5])

    def test_no_threshold_never_fabricated(self):
        trace = chart_trace({'rows': [{'timestamp': 'x'}], 'threshold': [0.4]})
        self.assertIsNone(trace['decision_threshold'])
        self.assertIsNone(trace['decision_thresholds'])

    def test_bad_shapes_or_nan_rejected_or_sanitized(self):
        self.assertIsNone(chart_trace({'rows': [{'timestamp': 1}], 'threshold': []}))
        self.assertIsNone(chart_trace({'rows': [{'timestamp': 1}], 'threshold': [float('nan')]}))
        trace = chart_trace({'rows': [{'timestamp': 1}, {'timestamp': 2}], 'threshold': [float('nan'), 1.0]})
        self.assertEqual(trace['scores'], [None, 1])
        self.assertIsNone(trace['decision_threshold'])
        json.dumps(trace, allow_nan=False)

    def test_long_results_bounded_extrema_and_original_timestamp(self):
        data = {'rows': [{'timestamp': f't-{i}'} for i in range(8000)],
                'threshold': [0.0] * 8000, 'decision_threshold': 2.0}
        data['threshold'][2231] = 999.0
        trace = chart_trace(data)
        self.assertLessEqual(len(trace['rows']), MAX_SAVED_POINTS)
        self.assertEqual(trace['total_samples'], 8000)
        self.assertTrue(trace['sampled'])
        self.assertIn(999.0, trace['scores'])
        self.assertEqual(trace['rows'][trace['scores'].index(999.0)]['timestamp'], 't-2231')
        self.assertLess(len(json.dumps(trace)), 130000)

    def test_history_details_legacy_and_new_traces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            location = root / 'outputs' / 'experiment_example' / 'reports'
            location.mkdir(parents=True)
            path = location / 'summary.json'
            trace = chart_trace({'rows': [{'timestamp': 'x'}], 'threshold': [2.0], 'decision_threshold': 1.0})
            summary = {'experiment': {'experiment_id': 'example', 'type': 'training'},
                       'training': {'algorithm': 'dummy'},
                       'results': [{'detector': 'dummy', 'status': 'success', 'metrics': {}, 'chart_trace': trace},
                                   {'detector': 'legacy', 'status': 'success', 'metrics': {}}]}
            path.write_text(json.dumps(summary), encoding='utf-8')
            with patch.object(services, 'PROJECT_ROOT', root):
                details = services.get_experiment_details('outputs/experiment_example/reports/summary.json')
            self.assertEqual(details['rows'][0]['chart_trace'], trace)
            self.assertIsNone(details['rows'][1]['chart_trace'])


if __name__ == '__main__':
    unittest.main()

class ReportHttpRoundTripTests(unittest.TestCase):
    """Actually save and fetch an experiment over authenticated HTTP."""

    def test_archive_http_save_and_read(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from scripts import visualization_server as app
        from scripts.server.home_auth import HomeAuth

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'configs' / 'experiments').mkdir(parents=True)
            auth = HomeAuth(root)
            server = None
            with patch.object(app, 'ROOT', root), patch.object(app, 'HOME_AUTH', auth):
                server = ThreadingHTTPServer(('127.0.0.1', 0), app.DashboardHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                origin = f'http://127.0.0.1:{server.server_port}'

                def send(path, body=None, cookie=''):
                    headers = {'Content-Type': 'application/json'}
                    if cookie:
                        headers['Cookie'] = cookie
                    request = Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                                      headers=headers)
                    with urlopen(request, timeout=5) as response:
                        return json.load(response), response.headers.get('Set-Cookie', '')

                try:
                    send('/api/auth/setup', {'username': 'admin', 'password': 'long-password-for-testing'})
                    _, cookie = send('/api/auth/login', {'username': 'admin', 'password': 'long-password-for-testing'})
                    cookie = cookie.split(';', 1)[0]
                    payload = {
                        'type': 'evaluation',
                        'model_id': 42, 'model_name': 'sample', 'model_algorithm': 'zscore',
                        'config': {'algorithm': 'zscore', 'dataset': 'datasets/history_sample.db', 'table': 'demo'},
                        'result': {'rows': [{'timestamp': 't0'}, {'timestamp': 't1'}],
                                   'threshold': [0.2, 9.1], 'decision_threshold': 3.0,
                                   'pred_labels': [0, 1], 'metrics': {'accuracy': 1.0}},
                    }
                    receipt = app.EXECUTION_REGISTRY.issue(
                        kind='evaluation', config=payload['config'], result=payload['result'],
                        identity={'model_id': 42, 'model_name': 'sample', 'model_algorithm': 'zscore'},
                        root=root)
                    saved, _ = send('/api/save-experiment', {'type':'evaluation',
                        'execution_receipt': receipt}, cookie)
                    self.assertEqual(saved['type'], 'evaluation')
                    with patch.object(services, 'PROJECT_ROOT', root):
                        detail, _ = send('/api/experiment-details?path=' +
                                         f"outputs/experiment_{saved['experiment_id']}/reports/summary.json", cookie=cookie)
                    trace = detail['rows'][0]['chart_trace']
                    self.assertEqual(trace['scores'], [0.2, 9.1])
                    self.assertEqual(trace['decision_threshold'], 3.0)
                    self.assertEqual(trace['pred_labels'], [0, 1])
                    self.assertEqual(detail['rows'][0]['metrics']['accuracy'], 1.0)
                    # The training archive uses the same persisted chart schema.
                    payload['type'] = 'training'
                    payload['config']['table'] = 'DEMO_train_temperature'
                    receipt = app.EXECUTION_REGISTRY.issue(
                        kind='training', config=payload['config'], result=payload['result'],
                        identity={}, root=root)
                    train_saved, _ = send('/api/save-experiment', {'type': 'training',
                        'execution_receipt':receipt}, cookie)
                    train_detail, _ = send('/api/experiment-details?path=' +
                                            f"outputs/experiment_{train_saved['experiment_id']}/reports/summary.json", cookie=cookie)
                    self.assertEqual(train_detail['experiment']['type'], 'training')
                    self.assertEqual(train_detail['rows'][0]['chart_trace']['scores'], [0.2, 9.1])
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()
