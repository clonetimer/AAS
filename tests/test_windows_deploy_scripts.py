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
    assert "if ([string]$manifest.source_commit -notmatch '^[0-9a-f]{40}$') {" in script
    assert "SHA-256 清单缺少有效的源码提交 SHA。" in script
    assert script.count("function Test-PackageManifest") == 1
    assert script.rstrip().endswith("}")
    for powershell_file in (root / "deploy" / "windows").glob("*.ps1"):
        assert powershell_file.read_bytes().startswith(bytes.fromhex("efbbbf"))


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
