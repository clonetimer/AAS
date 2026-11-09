# AAS V2 2026-11-05 UX/CSV candidate tests. Windows PowerShell 5.1 compatible.
# First install dependencies with LOCAL_TEST\Install-TestEnv.ps1.
# This scoped suite complements, but does not replace, Run-LocalTests.ps1 -Mode smoke/full.
[CmdletBinding()]
param([switch]$RequireNode)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing AAS test Python: $Python. Run LOCAL_TEST\Install-TestEnv.ps1 first."
}

$Suites = @(
    'tests/test_v2_auto_archive_upgrade.py',
    'tests/test_v2_csv_workspace_upgrade.py',
    'tests/test_v2_source_file_import_phase33.py',
    'tests/test_v2_model_bundle_upgrade.py',
    'tests/test_v2_score_only_upgrade.py',
    'tests/test_v2_historical_export_upgrade.py',
    'tests/test_v2_historical_frame_upgrade.py',
    'tests/test_v2_browse_relaxed_upgrade.py',
    'tests/test_telemetry_v2_browser_phase4.py',
    'tests/test_v2_simulation_injection_phase13.py',
    'tests/test_v2_simulation_hierarchical_phase16.py',
    'tests/test_simulation_record_filters.py',
    'tests/test_phase28_browser_hard_time_limits.py',
    'tests/test_phase27_browser_selection_export.py',
    'tests/test_upgrade_delivery_contract.py',
    'tests/test_upgrade_packaging_contract.py',
    'tests/test_csv_quality.py',
    'tests/test_phase47_import_metadata_wizard.py',
    'tests/test_experiment_comparison.py'
)

Push-Location $Root
try {
    Write-Host '[AAS V2 Upgrade] Running scoped candidate pytest regression...'
    # This single former demo test requires a pre-seeded SQLite database NOT
    # bundled in the original source-only media; never mark it passed.
    & $Python -m pytest -q @Suites -k 'not test_preinstalled_demo_record_removal_preserves_other_records'
    if ($LASTEXITCODE -ne 0) {
        throw "AAS V2 scoped pytest FAILED (exit $LASTEXITCODE)."
    }

    $NodeExe = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $NodeExe) {
        if ($RequireNode) { throw 'Node.js is not installed; JavaScript UI contracts were NOT tested.' }
        Write-Warning 'Node.js not found. JavaScript UI contracts are SKIPPED, not PASSED.'
    } else {
        $JsSuites = @(
            'tests/runner_csv_import_ui.cjs', 'tests/runner_csv_format_behavior.cjs',
            'tests/runner_score_only_ui.cjs', 'tests/v2_auto_archive_ui.cjs',
            'tests/csv_workspace_ui.cjs', 'tests/telemetry_v2_import_format_ui.cjs',
            'tests/telemetry_v2_import_quality_roles_ui.cjs', 'tests/report_comparison_metrics_ui.cjs'
        )
        foreach ($Script in $JsSuites) {
            & $NodeExe.Source $Script
            if ($LASTEXITCODE -ne 0) { throw "JavaScript contract FAILED: $Script (exit $LASTEXITCODE)." }
        }
    }
    Write-Host '[AAS V2 Upgrade] Scoped tests finished. This is NOT full Windows GUI acceptance.'
} finally {
    Pop-Location
}
