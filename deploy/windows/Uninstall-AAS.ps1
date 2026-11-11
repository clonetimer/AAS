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
if (-not (Test-Path -LiteralPath (Join-Path $Root '.aas-service.json'))) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', 8765)
        throw '检测到 8765 端口仍有服务，但没有 AAS 受控服务记录。请在原启动终端按 Ctrl+C 停止服务后再卸载。'
    } catch {
        if ($_.Exception.Message -like '检测到 8765 端口仍有服务*') { throw }
    } finally { $client.Dispose() }
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
