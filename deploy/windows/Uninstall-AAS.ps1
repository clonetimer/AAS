[CmdletBinding()]
param([switch]$DeleteUserData)

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$marker = Join-Path $Root '.aas-install.json'
if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $Root 'scripts\visualization_server.py') -PathType Leaf)) {
    throw '当前目录不是受本安装器管理的 AAS 安装目录，拒绝卸载。'
}

$answer = Read-Host '即将卸载 AAS 程序。输入 REMOVE 确认'
if ($answer -cne 'REMOVE') { Write-Host '已取消卸载。'; return }

$stopScript = Join-Path $Root 'Stop-AAS.ps1'
if (Test-Path -LiteralPath $stopScript) {
    & $stopScript
}
$entry = [IO.Path]::GetFullPath((Join-Path $Root 'scripts\\visualization_server.py'))
try {
    $runningAas = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ($_.CommandLine -match 'visualization_server\\.py' -and $_.ExecutablePath -and
             $_.ExecutablePath.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase))
        )
    })
} catch {
    throw "无法确认 AAS 服务是否已经停止；为保护运行数据，已取消卸载：$($_.Exception.Message)"
}
if ($runningAas.Count -gt 0) {
    $processIds = ($runningAas | ForEach-Object { [string]$_.ProcessId }) -join ', '
    throw "检测到 AAS 服务进程仍在运行（PID: $processIds）。请先停止服务；若由终端启动，请在原终端按 Ctrl+C。"
}

$parent = Split-Path -Parent $Root
$name = Split-Path -Leaf $Root
if ($DeleteUserData) {
    $dataAnswer = Read-Host '将永久删除 datasets、models 和 configs。输入 DELETE DATA 再确认'
    if ($dataAnswer -cne 'DELETE DATA') { Write-Host '已取消数据删除；安装目录未卸载。'; return }
} else {
    $backup = Join-Path $parent ("{0}_UserDataBackup_{1}" -f $name, (Get-Date -Format 'yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    foreach ($item in @('datasets','models','configs','config.yaml')) {
        $source = Join-Path $Root $item
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination $backup
        }
    }
    Write-Host "用户数据已保留到：$backup"
}

Set-Location -LiteralPath $parent
Remove-Item -LiteralPath $Root -Recurse -Force
Write-Host 'AAS 程序已卸载。'
