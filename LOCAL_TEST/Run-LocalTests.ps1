[CmdletBinding()]
param(
    [ValidateSet('smoke','full','migration')][string]$Mode = 'smoke',
    [string]$SourceDb = '',
    [string]$ReviewDb = '',
    [string]$GateReport = '',
    [switch]$Production,
    [int]$BatchTimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "Missing Python environment: $Python`nRun LOCAL_TEST\Install-TestEnv.ps1 first."
}
Push-Location $Root
try {
    $Arguments = @('LOCAL_TEST/run_local_tests.py','--mode',$Mode,
                   '--batch-timeout-seconds',[string]$BatchTimeoutSeconds)
    if ($Mode -eq 'migration') {
        if (-not $SourceDb -or -not $ReviewDb -or -not $GateReport) {
            throw 'Migration requires -SourceDb, -ReviewDb, and -GateReport.'
        }
        $Arguments += @('--source-db', $SourceDb, '--review-db', $ReviewDb,
                        '--gate-report', $GateReport)
        if ($Production) { $Arguments += '--production' }
    }
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Test failed (exit $LASTEXITCODE). Check outputs\phase53_windows_tests\" }
} finally {
    Pop-Location
}
