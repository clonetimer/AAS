from scripts.server import v2_user_csv_workspace as ws


def test_r3_task_reference_survives_workspace_reopen_and_blocks_delete(tmp_path):
    item = ws.import_csv(tmp_path, b"time_s,x\n0,1\n1,2\n", "persist.csv",
                         "test", {"rows": 2}, format_confirmed=True)
    ws.bind_task(tmp_path, "task-survives-restart", item["id"])

    # A fresh call reopens the persistent SQLite file; the reference remains.
    reopened = ws.list_datasets(tmp_path, "test")
    assert reopened[0]["id"] == item["id"]
    assert reopened[0]["active_refs"] == 1
    try:
        ws.delete_dataset(tmp_path, item["id"], confirm=True)
    except RuntimeError as exc:
        assert "正被任务引用" in str(exc)
    else:
        raise AssertionError("referenced CSV must not be deleted")

    ws.release_task(tmp_path, "task-survives-restart")
    assert ws.delete_dataset(tmp_path, item["id"], confirm=True)["deleted"] is True
