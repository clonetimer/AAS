[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$serviceFile = Join-Path $Root '.aas-service.json'
if (-not (Test-Path -LiteralPath $serviceFile)) {
    Write-Host '没有由 Start-AAS.cmd 管理的服务记录。若服务在终端中启动，请在原终端按 Ctrl+C。'
    return
}
$record = Get-Content -LiteralPath $serviceFile -Raw -Encoding UTF8 | ConvertFrom-Json
$processId = [int]$record.pid
$port = [int]$record.port
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $serviceFile -Force
    Write-Host '服务进程已退出，已清理过期服务记录。'
    return
}

$cim = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $processId) -ErrorAction SilentlyContinue
$entry = Join-Path $Root 'scripts\visualization_server.py'
if (-not $cim -or -not $cim.CommandLine -or
    $cim.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw '服务 PID 与当前 AAS 安装目录不匹配；为避免结束其他程序，停止操作。'
}

try {
    Invoke-RestMethod -UseBasicParsing -Method Post -Uri ("http://127.0.0.1:{0}/_aas/control/shutdown" -f $port) -Headers @{
        'X-AAS-Control-Token' = [string]$record.token
    } -TimeoutSec 5 | Out-Null
} catch {
    if (-not $Force) {
        throw "未能完成受控停止：$($_.Exception.Message)。可检查 logs/service.stderr.log；确认后使用 Stop-AAS.ps1 -Force。"
    }
}

$stopped = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { $stopped = $true; break }
}
if (-not $stopped -and $Force) {
    Stop-Process -Id $processId -Force
    Start-Sleep -Milliseconds 500
    $stopped = -not [bool](Get-Process -Id $processId -ErrorAction SilentlyContinue)
}
if (-not $stopped) { throw 'AAS 服务仍在运行。未强制结束进程；请检查日志或明确使用 -Force。' }
Remove-Item -LiteralPath $serviceFile -Force -ErrorAction SilentlyContinue
Write-Host 'AAS 服务已停止。用户数据库和模型文件未被修改。'
