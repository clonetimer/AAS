"""Windows acceptance helper should preserve real exit status and offline scope."""
from pathlib import Path


def test_upgrade_powershell_runner_has_explicit_v2_scope_and_no_false_pass():
    root=Path(__file__).resolve().parents[1]
    helper=root/'LOCAL_TEST'/'Run-UpgradeTests.ps1'
    assert helper.is_file()
    script=helper.read_text(encoding='utf-8')
    for marker in ('PSScriptRoot',r'.venv\Scripts\python.exe',
                   'tests/test_v2_auto_archive_upgrade.py',
                   'tests/test_v2_historical_frame_upgrade.py',
                   'tests/test_v2_csv_workspace_upgrade.py',
                   'tests/test_v2_model_bundle_upgrade.py',
                   'tests/test_upgrade_packaging_contract.py',
                   'tests/test_v2_browse_relaxed_upgrade.py',
                   'test_preinstalled_demo_record_removal_preserves_other_records',
                   '$LASTEXITCODE','Get-Command node','Write-Warning'):
        assert marker in script
    assert 'Run-LocalTests.ps1' in script


def test_delivery_report_links_windows_runner_and_avoids_stable_claim():
    root=Path(__file__).resolve().parents[1]
    report=(root/'docs'/'UPGRADE_STATUS_AND_NEXT_STEPS_20261105.md').read_text('utf-8')
    assert 'Run-UpgradeTests.ps1' in report
    assert '非 Windows Stable 验收版' in report
