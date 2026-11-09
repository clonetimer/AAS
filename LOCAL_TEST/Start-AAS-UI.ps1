[CmdletBinding()]
param([string]$ManagedImportRoot = '')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Missing .venv; run Install-TestEnv.ps1 first.' }
if ($ManagedImportRoot) {
    $env:AAS_BATCH_IMPORT_ROOT = (Resolve-Path $ManagedImportRoot).Path
}
Push-Location $Root
try {
    & $Python scripts/visualization_server.py --host 127.0.0.1 --port 8765
    if ($LASTEXITCODE -ne 0) { throw "Web server exited with code $LASTEXITCODE" }
} finally { Pop-Location }
