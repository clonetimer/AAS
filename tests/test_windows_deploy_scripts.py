from pathlib import Path


def test_offline_installer_checks_integrity_space_and_avoids_fixed_drive():
    root = Path(__file__).resolve().parents[1]
    script = (root / "deploy" / "windows" / "Deploy-AAS.ps1").read_text(encoding="utf-8")
    assert "Get-FileHash" in script
    assert "AvailableFreeSpace" in script
    assert "LOCALAPPDATA" in script
    assert "D:\\AAS" not in script
    assert "Remove-Item -LiteralPath $TargetDir -Recurse -Force" in script
    assert "package_manifest.json" in script
    assert "source_commit = $manifest.source_commit" in script


def test_service_shortcuts_use_local_token_and_preserve_user_data():
    root = Path(__file__).resolve().parents[1] / "deploy" / "windows"
    start = (root / "Start-AAS.ps1").read_text(encoding="utf-8")
    stop = (root / "Stop-AAS.ps1").read_text(encoding="utf-8")
    uninstall = (root / "Uninstall-AAS.ps1").read_text(encoding="utf-8")
    assert "127.0.0.1" in start
    assert "X-AAS-Control-Token" in stop
    assert "/_aas/control/shutdown" in stop
    assert "'datasets','models','configs','config.yaml'" in uninstall
    assert "DELETE DATA" in uninstall
