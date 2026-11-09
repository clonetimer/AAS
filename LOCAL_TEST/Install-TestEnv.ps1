[CmdletBinding()]
param([string]$PythonLauncher = 'py')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $Root
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & $PythonLauncher -3 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create virtual environment.' }
    }
    $Python = (Join-Path $Root '.venv\Scripts\python.exe')
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $Python -m pip install -r requirements.txt 'pytest>=8,<10'
    if ($LASTEXITCODE -ne 0) { throw 'Requirements installation failed.' }
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'pip check failed.' }
    # Use a .py file instead of python -c: Windows PowerShell 5.1 and native argument quoting differ.
    & $Python (Join-Path $PSScriptRoot 'verify_dependencies.py')
    if ($LASTEXITCODE -ne 0) { throw 'Core dependencies import failed.' }
    Write-Host 'Environment ready. Run LOCAL_TEST\Run-LocalTests.ps1 -Mode smoke'
} finally { Pop-Location }
