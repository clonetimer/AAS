from pathlib import Path


def test_csv_quickstart_states_independent_training_test_storage():
    root = Path(__file__).resolve().parents[1]
    guide = (root / "docs" / "G0_3_CSV_IMPORT_QUICKSTART.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "不写入历史遥测数据库" in guide
    assert "user_csv_workspaces/user_csv_workspace.sqlite" in guide
    assert "不写入历史遥测数据库" in readme
    assert "独立用户 CSV 工作区" in readme
    assert "历史库中的训练来源" not in guide
    assert "历史库中的测试来源" not in guide
