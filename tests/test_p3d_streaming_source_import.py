from __future__ import annotations

import gzip
import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.server.csv_quality import analyze_csv_rows
from scripts.server.telemetry_v2_source_import import source_operation
from tests.test_v2_source_file_import_phase33 import counts, dest, gzip_data, meta


def test_quality_analysis_accepts_one_pass_row_iterator():
    rows = iter([
        {'time_s': '0', 'speed': '1', 'label': '0'},
        {'time_s': '1', 'speed': '', 'label': '1'},
        {'time_s': '2', 'speed': '3', 'label': '1'},
    ])

    result = analyze_csv_rows(
        ['time_s', 'speed', 'label'], rows,
        time_column='time_s', label_column='label')

    assert result['row_count'] == 3
    assert result['missing_by_column'] == {'speed': 1}
    assert result['label_counts'] == {'0': 1, '1': 2, 'unknown': 0}


def test_file_backed_gzip_source_import_matches_legacy_contract(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,,1\n2,5,1\n')
    upload = tmp_path / 'run.csv.gz'
    upload.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    inspect = source_operation_file(
        db_path, upload, {'operation': 'inspect', 'filename': upload.name},
        source_digest=digest, source_size=len(raw))
    assert inspect['format'] == 'csv.gz'
    assert inspect['columns'] == ['time_s', 'adcs.rw.speed', 'label']

    config = meta(upload.name)
    legacy_preview = source_operation(db_path, raw, config)
    preview = source_operation_file(
        db_path, upload, config, source_digest=digest, source_size=len(raw))
    assert preview['status'] == 'preview'
    for key in ('row_count', 'column_count', 'missing_by_column', 'columns',
                'time_start', 'time_end', 'quality', 'label_status', 'sha256'):
        assert preview[key] == legacy_preview[key], key
    assert preview['missing_by_column'] == {'adcs.rw.speed': 1}
    assert counts(db_path) == [0, 0, 0, 0]

    committed = source_operation_file(
        db_path, upload, {**config, 'operation': 'commit'},
        source_digest=digest, source_size=len(raw))
    assert committed['status'] == 'imported'
    assert committed['row_count'] == 3
    assert counts(db_path) == [1, 1, 1, 3]
    with sqlite3.connect(db_path) as conn:
        source = conn.execute(
            'SELECT source_sha256, source_bytes FROM v2_sources').fetchone()
    assert source == (digest, len(raw))


def test_file_backed_gzip_import_rejects_expansion_beyond_limit(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    upload = tmp_path / 'bomb.csv.gz'
    upload.write_bytes(gzip.compress(b'a' * 12_000_100))

    with pytest.raises(ValueError, match='12MB'):
        source_operation_file(
            db_path, upload, {'operation': 'inspect', 'filename': upload.name})
    assert counts(db_path) == [0, 0, 0, 0]


def test_file_backed_import_cancellation_before_commit_leaves_database_unchanged(tmp_path):
    from scripts.server.telemetry_v2_source_import import (
        SourceImportCancelled, source_operation_file)

    db_path = dest(tmp_path)
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,4,1\n')
    upload = tmp_path / 'cancel.csv.gz'
    upload.write_bytes(raw)
    config = meta(upload.name, operation='commit')

    with pytest.raises(SourceImportCancelled):
        source_operation_file(
            db_path, upload, config,
            source_digest=hashlib.sha256(raw).hexdigest(), source_size=len(raw),
            cancel_requested=lambda: True)
    assert counts(db_path) == [0, 0, 0, 0]


def test_import_source_http_uses_chunked_disk_staging_and_durable_task(tmp_path, monkeypatch):
    import base64
    import json
    import time
    from types import SimpleNamespace

    import scripts.server.handler_post as post_module
    from scripts.server.handler_post import PostRoutesMixin
    from scripts.server.task_runtime import TaskRuntime

    db_path = dest(tmp_path)
    monkeypatch.setattr(post_module, 'active_path', lambda _root: db_path)
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(tmp_path)
    responses = []
    main = SimpleNamespace(
        ROOT=tmp_path,
        create_task=runtime.create_task,
        bind_task_thread=runtime.bind_task_thread,
        update_task_progress=runtime.update_task_progress,
        is_cancel_requested=runtime.is_cancel_requested,
        set_task_cancellable=runtime.set_task_cancellable,
        complete_task=runtime.complete_task,
        json_response=lambda _handler, payload, status=200: responses.append((status, payload)),
    )
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,4,1\n')
    metadata = {'filename': 'async.csv.gz', 'operation': 'inspect'}
    headers = {
        'Content-Length': str(len(raw)),
        'X-AAS-Import-Metadata': base64.b64encode(json.dumps(metadata).encode()).decode(),
    }

    class TrackingReader:
        def __init__(self, value):
            import io
            self._stream = io.BytesIO(value)
            self.max_requested = 0
        def read(self, size=-1):
            self.max_requested = max(self.max_requested, size)
            assert 0 < size <= 256 * 1024
            return self._stream.read(size)

    class FakeHandler(PostRoutesMixin):
        path = '/api/v2/telemetry/import-source'
        server = SimpleNamespace(aas_application=main)
        rfile = TrackingReader(raw)
        def __init__(self):
            self.headers = headers
        def _handle_auth_post(self, _parsed, _main):
            return False
        def _require_auth(self, _parsed, _main, is_post=False):
            return True

    handler = FakeHandler()
    handler._dispatch_POST()

    assert responses[0][0] == 202
    task_id = responses[0][1]['task_id']
    assert responses[0][1]['status'] == 'accepted'
    assert handler.rfile.max_requested <= 256 * 1024
    deadline = time.monotonic() + 5
    status = runtime.get_public_task_status(task_id)
    while status['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(0.01)
        status = runtime.get_public_task_status(task_id)
    assert status['status'] == 'completed', status
    assert status['task_type'] == 'csv_import'
    assert status['result']['columns'] == ['time_s', 'adcs.rw.speed', 'label']
    assert 'import_context' not in status['result']
    assert 'value' not in str(status['result'].get('quality', {}).get('issues', []))
    assert not list((tmp_path / 'outputs' / 'import_staging').glob('*'))
    journal = (tmp_path / 'outputs' / 'task_state' / f'{task_id}.json').read_text('utf-8')
    assert 'time_s,adcs.rw.speed,label' not in journal
    restored = TaskRuntime.for_root(tmp_path).get_public_task_status(task_id)
    assert restored['status'] == 'completed'
    assert restored['result']['columns'] == ['time_s', 'adcs.rw.speed', 'label']
    assert 'import_context' not in restored['result']


def test_file_backed_gb18030_semicolon_selected_columns_preserve_labels(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    raw = 'time_s;adcs.rw.speed;fault_state;说明\n0;1.0;0;正常\n1;2.0;1;故障\n'.encode('gb18030')
    upload = tmp_path / '现场.csv'
    upload.write_bytes(raw)
    inspected = source_operation_file(
        db_path, upload, {'filename': upload.name, 'operation': 'inspect',
                          'csv_encoding': 'auto', 'csv_delimiter': 'auto',
                          'label_column': 'fault_state'})
    assert inspected['csv_encoding'] == 'gb18030'
    assert inspected['csv_delimiter'] == ';'
    assert inspected['quality']['label_counts'] == {'0': 1, '1': 1, 'unknown': 0}

    config = meta(upload.name, csv_encoding='gb18030', csv_delimiter=';', format_confirmed=True)
    config['config'].update(
        time_column='time_s', label_column='fault_state',
        selected_columns=['adcs.rw.speed'])
    config['config']['column_metadata'] = {
        'adcs.rw.speed': {'subsystem': 'ADCS', 'device': 'RW1', 'variable': 'speed', 'unit': 'rad/s'}}
    preview = source_operation_file(db_path, upload, config)
    assert preview['row_count'] == 2
    assert preview['column_count'] == 1
    assert preview['label_status'] == 'provided'
    assert counts(db_path) == [0, 0, 0, 0]
    committed = source_operation_file(db_path, upload, {**config, 'operation': 'commit'})
    assert committed['row_count'] == 2
    assert counts(db_path) == [1, 1, 1, 2]


def test_file_backed_csv_stream_is_sorted_for_storage_but_keeps_source_ordinals(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    raw = gzip_data('time_s,adcs.rw.speed,label\n2,20,0\n0,10,0\n1,15,0\n')
    upload = tmp_path / 'unsorted.csv.gz'
    upload.write_bytes(raw)
    config = meta(upload.name)
    committed = source_operation_file(db_path, upload, {**config, 'operation': 'commit'})
    assert committed['time_start'] == '0'
    assert committed['time_end'] == '2'
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute('''SELECT s.timestamp_raw,s.value_num,s.source_ordinal
            FROM v2_samples s WHERE s.segment_id=? ORDER BY s.time_sort_num''',
            (committed['channels'][0]['segment_id'],)).fetchall()
    assert rows == [('0', 10.0, 1), ('1', 15.0, 2), ('2', 20.0, 0)]


def test_file_backed_csv_stream_checks_cancellation_during_row_staging(tmp_path):
    from scripts.server.import_cancellation import SourceImportCancelled
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    rows = ''.join(f'{i},{i * 0.5},0\n' for i in range(600))
    raw = gzip_data('time_s,adcs.rw.speed,label\n' + rows)
    upload = tmp_path / 'cancel-mid-parse.csv.gz'
    upload.write_bytes(raw)
    config = meta(upload.name, operation='commit')
    calls = {'count': 0}

    def cancel_after_first_batch():
        calls['count'] += 1
        return calls['count'] >= 3

    with pytest.raises(SourceImportCancelled):
        source_operation_file(
            db_path, upload, config, source_digest=hashlib.sha256(raw).hexdigest(),
            source_size=len(raw), cancel_requested=cancel_after_first_batch)
    assert calls['count'] >= 3
    assert counts(db_path) == [0, 0, 0, 0]


def test_file_backed_csv_cancelled_at_commit_boundary_never_writes(tmp_path):
    from scripts.server.import_cancellation import SourceImportCancelled
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,4,1\n')
    upload = tmp_path / 'cancel-boundary.csv.gz'
    upload.write_bytes(raw)
    config = meta(upload.name, operation='commit')
    observed = []

    def refuse_commit():
        observed.append(True)
        return False

    with pytest.raises(SourceImportCancelled):
        source_operation_file(
            db_path, upload, config, source_digest=hashlib.sha256(raw).hexdigest(),
            source_size=len(raw), before_commit=refuse_commit)
    assert observed == [True]
    assert counts(db_path) == [0, 0, 0, 0]


def test_file_backed_gzip_rejects_trailing_bytes_and_corruption(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    trailing = tmp_path / 'trailing.csv.gz'
    trailing.write_bytes(gzip_data('time_s,speed,label\n0,1,0\n') + b'extra')
    with pytest.raises(ValueError, match='尾部'):
        source_operation_file(db_path, trailing, {'filename': trailing.name, 'operation': 'inspect'})
    broken = tmp_path / 'broken.csv.gz'
    broken.write_bytes(b'\x1f\x8bbroken')
    with pytest.raises(ValueError, match='GZIP'):
        source_operation_file(db_path, broken, {'filename': broken.name, 'operation': 'inspect'})
    assert counts(db_path) == [0, 0, 0, 0]


def test_file_backed_csv_progress_is_monotonic_across_upload_processing_stages(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    rows = ''.join(f'{i},{i * 0.25},0\n' for i in range(1200))
    raw = gzip_data('time_s,adcs.rw.speed,label\n' + rows)
    upload = tmp_path / 'progress.csv.gz'
    upload.write_bytes(raw)
    values = []

    source_operation_file(
        db_path, upload, meta(upload.name),
        progress=lambda value, _message, _stage: values.append(value))

    assert values
    assert values == sorted(values)
    assert values[-1] >= 90


def test_file_backed_stream_import_rejects_overlapping_channel_timestamps(tmp_path):
    from scripts.server.telemetry_v2_source_import import source_operation_file

    db_path = dest(tmp_path)
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,3,0\n1,4,1\n')
    upload = tmp_path / 'overlap.csv.gz'
    upload.write_bytes(raw)
    config = meta(upload.name)
    source_operation_file(db_path, upload, {**config, 'operation': 'commit'})

    with pytest.raises(ValueError, match='重叠'):
        source_operation_file(db_path, upload, {**config, 'operation': 'commit'})
    assert counts(db_path) == [1, 1, 1, 2]


def test_import_source_http_cancel_reaches_worker_and_cleans_staged_file(tmp_path, monkeypatch):
    import base64
    import json
    import threading
    import time
    from types import SimpleNamespace

    import scripts.server.handler_post as post_module
    import scripts.server.telemetry_v2_source_import as source_module
    from scripts.server.handler_post import PostRoutesMixin
    from scripts.server.import_cancellation import SourceImportCancelled
    from scripts.server.task_runtime import TaskRuntime

    db_path = dest(tmp_path)
    monkeypatch.setattr(post_module, 'active_path', lambda _root: db_path)
    started = threading.Event()
    release = threading.Event()

    def blocking_import(_db, _path, _metadata, **kwargs):
        started.set()
        assert release.wait(5)
        if kwargs['cancel_requested']():
            raise SourceImportCancelled('cancelled')
        return {'status': 'preview', 'columns': ['time_s', 'adcs.rw.speed']}

    monkeypatch.setattr(source_module, 'source_operation_file', blocking_import)
    runtime = TaskRuntime(resource_tracker=None)
    runtime.enable_recovery(tmp_path)
    responses = []
    main = SimpleNamespace(
        ROOT=tmp_path, create_task=runtime.create_task,
        bind_task_thread=runtime.bind_task_thread,
        update_task_progress=runtime.update_task_progress,
        is_cancel_requested=runtime.is_cancel_requested,
        set_task_cancellable=runtime.set_task_cancellable,
        complete_task=runtime.complete_task,
        json_response=lambda _handler, payload, status=200: responses.append((status, payload)),
    )
    raw = gzip_data('time_s,adcs.rw.speed,label\n0,1,0\n')
    metadata = {'filename': 'cancel.csv.gz', 'operation': 'inspect'}
    headers = {
        'Content-Length': str(len(raw)),
        'X-AAS-Import-Metadata': base64.b64encode(json.dumps(metadata).encode()).decode(),
    }

    class Reader:
        def __init__(self, value):
            import io
            self.stream = io.BytesIO(value)
        def read(self, size=-1):
            assert 0 < size <= 256 * 1024
            return self.stream.read(size)

    class FakeHandler(PostRoutesMixin):
        path = '/api/v2/telemetry/import-source'
        server = SimpleNamespace(aas_application=main)
        rfile = Reader(raw)
        def __init__(self): self.headers = headers
        def _handle_auth_post(self, _parsed, _main): return False
        def _require_auth(self, _parsed, _main, is_post=False): return True

    FakeHandler()._dispatch_POST()
    assert responses[0][0] == 202
    task_id = responses[0][1]['task_id']
    assert started.wait(5)
    assert runtime.request_cancel_task(task_id)['accepted'] is True
    release.set()
    deadline = time.monotonic() + 5
    status = runtime.get_public_task_status(task_id)
    while status['status'] not in ('cancelled', 'failed') and time.monotonic() < deadline:
        time.sleep(0.01)
        status = runtime.get_public_task_status(task_id)
    assert status['status'] == 'cancelled', status
    assert not (tmp_path / 'outputs' / 'import_staging' / f'{task_id}.upload').exists()


def test_stream_format_detection_validates_encoding_beyond_initial_sample(tmp_path):
    from scripts.server.csv_format import resolve_csv_stream_format

    upload = tmp_path / 'late-gb18030.csv'
    rows = ['time_s;speed;label;note']
    rows.extend(f'{index};{index};0;ok' for index in range(700))
    rows.append('700;700;0;正常')
    upload.write_bytes(('\n'.join(rows) + '\n').encode('gb18030'))

    assert resolve_csv_stream_format(upload) == ('gb18030', ';')


def test_stream_format_detection_rejects_binary_nul_anywhere_in_file(tmp_path):
    from scripts.server.csv_format import resolve_csv_stream_format

    upload = tmp_path / 'binary-tail.csv'
    upload.write_bytes(b'time_s,speed,label\n0,1,0\n' + b' ' * 9000 + b'\x00')
    with pytest.raises(ValueError, match='二进制空字符'):
        resolve_csv_stream_format(upload)


def test_csv_import_failure_diagnostics_are_actionable_and_path_free():
    from scripts.server.task_diagnostics import diagnose_failure

    gzip_failure = diagnose_failure(ValueError('GZIP 损坏 /private/tmp/source.gz'), 'csv_import', 'csv_import')
    assert gzip_failure['code'] == 'csv_compression_invalid'
    assert '/private/tmp' not in str(gzip_failure)
    csv_failure = diagnose_failure(ValueError('CSV 第 12 行参量 speed 不是数值'), 'csv_import', 'csv_import')
    assert csv_failure['code'] == 'csv_content_invalid'
    assert csv_failure['recovery_label'] == '返回文件检查'
