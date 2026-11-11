from pathlib import Path
import os
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser is available on Windows runners")
def test_all_windows_deployment_scripts_parse():
    root = Path(__file__).resolve().parents[1]
    scripts = sorted((root / "deploy" / "windows").glob("*.ps1"))
    assert scripts, "No Windows deployment PowerShell scripts were found"
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell executable is not available")

    for path in scripts:
        literal_path = str(path).replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'; "
            "$tokens = $null; $errors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{literal_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { [Console]::Out.WriteLine($_.ToString()) }; exit 1 "
            "}; "
            "[Console]::Out.WriteLine('POWERSHELL_PARSE_OK')"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"PowerShell parse failed: {path}\n{result.stdout}\n{result.stderr}"
        )
        assert "POWERSHELL_PARSE_OK" in result.stdout
