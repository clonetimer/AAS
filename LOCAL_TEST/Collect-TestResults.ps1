[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ReportFolder)
$ErrorActionPreference = 'Stop'
$Source = (Resolve-Path $ReportFolder).Path
if (-not (Test-Path (Join-Path $Source 'local_test_summary.json'))) {
    throw 'This folder does not contain a Phase 53 local test summary.'
}
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Archive = Join-Path $Root ("Phase53_Windows_TestResults_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.zip')
# Only report logs and JSON from this single run. Never include databases, targets,
# source telemetry, models, or the whole outputs directory.
$files = Get-ChildItem $Source -File | Where-Object { $_.Extension -in @('.json','.log') }
if (-not $files) { throw 'No report files to archive.' }
Compress-Archive -LiteralPath $files.FullName -DestinationPath $Archive -ErrorAction Stop
Write-Host "Results archive: $Archive"
Write-Warning 'Logs may contain absolute Windows paths or other local metadata. Inspect before sharing.'
