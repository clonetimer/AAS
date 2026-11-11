[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$entry = Join-Path $Root 'scripts\visualization_server.py'
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $Root '.python38\python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw '未找到 AAS Python 环境，请先运行离线部署程序。' }
if (-not (Test-Path -LiteralPath $entry)) { throw '未找到 AAS 服务入口 scripts\visualization_server.py。' }

$serviceFile = Join-Path $Root '.aas-service.json'
$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-PortOpen([int]$Value) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Value)
        return $true
    } catch { return $false }
    finally { $client.Dispose() }
}

if (Test-PortOpen $Port) {
    if (Test-Path -LiteralPath $serviceFile) {
        try {
            $old = Get-Content -LiteralPath $serviceFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $process = Get-Process -Id ([int]$old.pid) -ErrorAction Stop
            $cim = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$old.pid) -ErrorAction Stop
            if ($cim.CommandLine -and $cim.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Write-Host "AAS 已在运行：http://127.0.0.1:$Port/"
                if ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port/" }
                exit 0
            }
        } catch { }
    }
    throw "端口 $Port 已被其他进程占用。请检查端口或使用 -Port 指定其他端口。"
}

if (Test-Path -LiteralPath $serviceFile) { Remove-Item -LiteralPath $serviceFile -Force }
$stdout = Join-Path $logDir 'service.stdout.log'
$stderr = Join-Path $logDir 'service.stderr.log'
$argumentLine = '"' + $entry + '" --host 127.0.0.1 --port ' + $Port
$process = Start-Process -FilePath $python -ArgumentList $argumentLine -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) { break }
    if (Test-PortOpen $Port) { $ready = $true; break }
}
if (-not $ready) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    $tail = ''
    if (Test-Path $stderr) { $tail = (Get-Content $stderr -Tail 20) -join [Environment]::NewLine }
    throw ("AAS 服务未能在 30 秒内启动。日志：{0}{1}{2}" -f $stderr, [Environment]::NewLine, $tail)
}

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$token = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
$record = [ordered]@{
    pid = $process.Id
    port = $Port
    token = $token
    started_utc = (Get-Date).ToUniversalTime().ToString('o')
}
$record | ConvertTo-Json -Compress | Set-Content -LiteralPath $serviceFile -Encoding UTF8

Write-Host "AAS 已启动：http://127.0.0.1:$Port/"
Write-Host "服务进程 PID：$($process.Id)"
Write-Host "日志：$stdout；$stderr"
if ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port/" }
