from scripts.server import v2_user_csv_workspace as ws


def test_r3_task_reference_survives_workspace_reopen_and_blocks_delete(tmp_path):
    item = ws.import_csv(tmp_path, b'time_s,x\n0,1\n1,2\n', 'persist.csv',
                         'test', {'rows': 2}, format_confirmed=True)
    ws.set_import_result(tmp_path, item['id'], {'source_id': None}, status='imported')
    ws.bind_task(tmp_path, 'task-survives-restart', item['id'])

    reopened = ws.list_datasets(tmp_path, 'test')
    assert reopened[0]['id'] == item['id']
    assert reopened[0]['active_refs'] == 1
    assert ws.reconcile_task_refs(tmp_path, lambda _task: {'status': 'running'}) == 0
    try:
        ws.delete_dataset(tmp_path, item['id'], confirm=True)
    except RuntimeError as exc:
        assert '正被任务引用' in str(exc)
    else:
        raise AssertionError('referenced CSV must not be deleted')

    assert ws.reconcile_task_refs(tmp_path, lambda _task: {'status': 'interrupted'}) == 1
    assert ws.delete_dataset(tmp_path, item['id'], confirm=True)['deleted'] is True


def test_r1_duplicate_commit_guard_and_interrupted_import_recovery(tmp_path):
    item = ws.import_csv(tmp_path, b't,x\n0,1\n', 'retry.csv', 'train', {},
                         format_confirmed=True, import_key='same-import')
    assert ws.claim_import(tmp_path, item['id'], task_id='csv-task-1') is True
    try:
        ws.claim_import(tmp_path, item['id'], task_id='csv-task-2',
                        status_lookup=lambda _task: {'status': 'running'})
    except RuntimeError as exc:
        assert '正在运行' in str(exc)
    else:
        raise AssertionError('concurrent commit must be rejected')

    assert ws.reconcile_imports(tmp_path,
        lambda _task: {'status': 'interrupted'}) == 1
    assert ws.get_dataset(tmp_path, item['id'])['import_status'] == 'failed'
    assert ws.claim_import(tmp_path, item['id'], task_id='csv-task-3') is True
    assert ws.get_dataset(tmp_path, item['id'])['import_status'] == 'importing'


def test_r3_unknown_task_status_fails_closed_and_importing_csv_cannot_be_deleted(tmp_path):
    item = ws.import_csv(tmp_path, b'time_s,x\n0,1\n', 'unknown-status.csv', 'test', {},
                         format_confirmed=True)
    ws.set_import_result(tmp_path, item['id'], {}, status='importing')
    try:
        ws.delete_dataset(tmp_path, item['id'], confirm=True)
    except RuntimeError as exc:
        assert '正在导入' in str(exc)
    else:
        raise AssertionError('an importing CSV must not be deleted')

    ws.set_import_result(tmp_path, item['id'], {'source_id': None}, status='imported')
    ws.bind_task(tmp_path, 'task-unknown', item['id'])
    assert ws.reconcile_task_refs(tmp_path, lambda _task: None) == 0
    assert ws.list_datasets(tmp_path, 'test')[0]['active_refs'] == 1
    try:
        ws.delete_dataset(tmp_path, item['id'], confirm=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError('unknown task status must retain the deletion guard')
