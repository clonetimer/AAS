param(
    [string]$DatabaseSource = '',
    [int]$Port = 8765,
    [switch]$SkipDependencies
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { py -3 -m venv (Join-Path $repo '.venv') }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python 3 虚拟环境创建失败' }
if (-not $SkipDependencies) { & $python -m pip install -r (Join-Path $repo 'requirements.txt') }
$datasets = Join-Path $repo 'datasets'
New-Item -ItemType Directory -Force -Path $datasets | Out-Null
$target = Join-Path $datasets 'aas_telemetry_v2.sqlite'
$legacy = Join-Path $datasets 'aas_telemetry.sqlite'
if ($DatabaseSource) {
    $source = (Resolve-Path -LiteralPath $DatabaseSource).Path
    if (([IO.Path]::GetFullPath($source)) -eq ([IO.Path]::GetFullPath($target))) {
        throw '数据库来源与安装目标相同；请省略 -DatabaseSource 并使用已安装数据库'
    }
    if (Test-Path -LiteralPath $target) { throw '目标数据库已存在；不会覆盖。请先核对并单独迁移' }
    Copy-Item -LiteralPath $source -Destination $target
}
if (-not $DatabaseSource -and -not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $legacy)) {
    Copy-Item -LiteralPath $legacy -Destination $target
    Write-Host "检测到旧文件名数据库，已复制到 V2 目标：$legacy"
}
if (-not (Test-Path -LiteralPath $target)) {
    $rawDir = Join-Path $datasets 'source_csv_gz'
    $targetsZip = Join-Path $datasets 'source_labels\targets.zip'
    $rawFiles = @(Get-ChildItem -LiteralPath $rawDir -Filter '*.csv.gz' -File -ErrorAction SilentlyContinue)
    if ($rawFiles.Count -gt 0 -and (Test-Path -LiteralPath $targetsZip)) {
        & $python -m scripts.rebuild_target_labeled_v2 --input-dir $rawDir --targets $targetsZip --output $target
        if ($LASTEXITCODE -ne 0) { throw '从原始 CSV 和 targets.zip 构建 V2 数据库失败' }
        Write-Host "已从 $($rawFiles.Count) 份原始 CSV 构建 V2 数据库"
    } elseif ($rawFiles.Count -eq 0 -and -not (Test-Path -LiteralPath $targetsZip)) {
        & $python -m scripts.create_empty_v2 --out $target
        if ($LASTEXITCODE -ne 0) { throw '创建空 V2 数据库失败' }
    } else {
        throw '原始 CSV 与 targets.zip 不完整；拒绝创建空库，请核对 datasets/source_csv_gz 与 datasets/source_labels'
    }
} else {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = Join-Path $datasets "aas_telemetry_v2.before_frame_$stamp.sqlite"
    Copy-Item -LiteralPath $target -Destination $backup
    & $python -m scripts.migrate_v2_coordinates --db $target --apply
    if ($LASTEXITCODE -ne 0) { throw "迁移失败；备份位于 $backup" }
    Write-Host "数据库迁移备份：$backup"
}
Write-Host '安装完成。启动服务：'
Write-Host ".\.venv\Scripts\python.exe scripts\visualization_server.py --host 127.0.0.1 --port $Port"
