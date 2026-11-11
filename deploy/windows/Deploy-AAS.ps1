# AAS Windows 10/11 x64 offline installer.
[CmdletBinding()]
param(
    [string]$TargetDir = '',
    [string]$PythonExe = '',
    [string]$DatabaseSource = '',
    [switch]$SkipDatabase,
    [switch]$StartAfterDeploy,
    [switch]$Portable
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$Pkg = $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogPath = Join-Path $env:TEMP ("AAS_Deploy_{0}.log" -f $Stamp)
$CreatedTarget = $false
$Step = '初始化'

function Write-Info([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message)
}

function Test-Py38([string]$Executable) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    try {
        $probe = & $Executable -c "import struct,sys; print('%s|%s' % (sys.version_info[:2] == (3,8), struct.calcsize('P')*8))" 2>$null
        return ($LASTEXITCODE -eq 0 -and (($probe -join '').Trim() -eq 'True|64'))
    } catch { return $false }
}

function Resolve-Py38 {
    if ($PythonExe) {
        if (Test-Py38 $PythonExe) { return (Resolve-Path -LiteralPath $PythonExe).Path }
        throw "指定的 Python 不是可用的 64 位 Python 3.8：$PythonExe"
    }

    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python38\python.exe')
    }
    $candidates += 'C:\Python38\python.exe'
    foreach ($candidate in $candidates) {
        if (Test-Py38 $candidate) { return $candidate }
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $listed = & $launcher.Source -0p 2>$null
        foreach ($line in @($listed)) {
            if ($line -match '(?i)([A-Z]:\\.*?python\.exe)\s*$') {
                $candidate = $Matches[1].Trim()
                if (Test-Py38 $candidate) { return $candidate }
            }
        }
    }
    return $null
}

function Install-PortableRuntime([string]$InstallRoot) {
    $runtimeDir = Join-Path $InstallRoot '.python38'
    $python = Join-Path $runtimeDir 'python.exe'
    $runtimeZip = Join-Path $Pkg '程序\Python便携版\python-3.8.10-embed-amd64.zip'
    $pipWheel = Join-Path $Pkg '程序\Python便携版\pip-24.3.1-py3-none-any.whl'
    if (-not (Test-Path -LiteralPath $runtimeZip) -or -not (Test-Path -LiteralPath $pipWheel)) {
        throw '离线介质缺少 Python 便携运行时或 pip wheel。'
    }
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    Expand-Archive -LiteralPath $runtimeZip -DestinationPath $runtimeDir -Force
    $pth = @('python38.zip', '.', 'Lib\site-packages', $InstallRoot, 'import site')
    Set-Content -LiteralPath (Join-Path $runtimeDir 'python38._pth') -Value $pth -Encoding ASCII
    $site = Join-Path $runtimeDir 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $site | Out-Null
    $pipZip = Join-Path $runtimeDir 'pip-wheel.zip'
    Copy-Item -LiteralPath $pipWheel -Destination $pipZip -Force
    Expand-Archive -LiteralPath $pipZip -DestinationPath $site -Force
    Remove-Item -LiteralPath $pipZip -Force
    if (-not (Test-Py38 $python)) { throw 'Python 便携运行时解压后校验失败。' }
    & $python -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Python 便携版 pip 引导失败。' }
    return $python
}

function Get-RequiredFreeBytes {
    if ($SkipDatabase) { return [int64](5GB) }
    if ($DatabaseSource) {
        $sourceBytes = (Get-Item -LiteralPath $DatabaseSource).Length
        return [int64]([Math]::Max(5GB, ($sourceBytes * 2) + 2GB))
    }
    return [int64](35GB)
}

function Test-PackageManifest {
    $manifestPath = Join-Path $Pkg '校验\双坐标文件哈希.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw '缺少 SHA-256 清单：校验\双坐标文件哈希.json'
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.entries -or $manifest.entries.PSObject.Properties.Count -lt 20) {
        throw 'SHA-256 清单为空或条目数量异常。'
    }
    $rootPrefix = [IO.Path]::GetFullPath($Pkg).TrimEnd('\') + '\'
    $checked = 0
    foreach ($property in $manifest.entries.PSObject.Properties) {
        $relative = [string]$property.Name
        if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') {
            throw "SHA-256 清单包含非法路径：$relative"
        }
        $fullPath = [IO.Path]::GetFullPath((Join-Path $Pkg ($relative -replace '/', '\')))
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "SHA-256 清单路径越界：$relative"
        }
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "离线介质缺少文件：$relative"
        }
        $file = Get-Item -LiteralPath $fullPath
        if ([int64]$property.Value.bytes -ne [int64]$file.Length) {
            throw "文件大小与清单不一致：$relative"
        }
        $actual = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$property.Value.sha256).ToLowerInvariant()) {
            throw "SHA-256 校验失败：$relative"
        }
        $checked++
    }
    Write-Info "SHA-256 校验通过：$checked 个文件；源码版本 $($manifest.source_commit)"
    return $manifest
}

try {
    if ([string]::IsNullOrWhiteSpace($TargetDir)) {
        if ($env:LOCALAPPDATA) { $TargetDir = Join-Path $env:LOCALAPPDATA 'AAS' }
        else { $TargetDir = Join-Path $env:USERPROFILE 'AAS' }
    }
    $TargetDir = [IO.Path]::GetFullPath($TargetDir)
    if ($DatabaseSource -and $SkipDatabase) { throw '-DatabaseSource 与 -SkipDatabase 不能同时使用。' }
    if ($DatabaseSource -and -not (Test-Path -LiteralPath $DatabaseSource -PathType Leaf)) {
        throw "旧数据库文件不存在：$DatabaseSource"
    }
    if ($DatabaseSource) { $DatabaseSource = (Resolve-Path -LiteralPath $DatabaseSource).Path }
    if (Test-Path -LiteralPath $TargetDir) {
        throw "目标目录已经存在，安装器不会覆盖：$TargetDir。请选择一个新的目录。"
    }

    Start-Transcript -LiteralPath $LogPath -Force | Out-Null
    Write-Info "离线包目录：$Pkg"
    Write-Info "安装目标：$TargetDir"
    Write-Info "部署日志：$LogPath"

    $Step = '1/8 介质完整性'
    $manifest = Test-PackageManifest
    $installer = Join-Path $Pkg '程序\Python安装包\python-3.8.10-amd64.exe'
    $wheelDir = Join-Path $Pkg '程序\运行依赖\wheelhouse'
    $lockFile = Join-Path $Pkg '程序\运行依赖\锁定版本.txt'
    $sourceZip = Join-Path $Pkg '程序\AAS_源码.zip'
    foreach ($required in @($installer, $wheelDir, $lockFile, $sourceZip)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "离线包缺少必需内容：$required" }
    }
    $lockedCount = @(Get-Content -LiteralPath $lockFile | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }).Count
    $wheelCount = @(Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl' -File).Count
    if ($wheelCount -ne $lockedCount) {
        throw "wheelhouse 数量 $wheelCount 与锁定依赖数量 $lockedCount 不一致。"
    }

    $Step = '2/8 磁盘空间预检'
    $parent = Split-Path -Parent $TargetDir
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $driveRoot = [IO.Path]::GetPathRoot($TargetDir)
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    $requiredBytes = Get-RequiredFreeBytes
    if ($drive.AvailableFreeSpace -lt $requiredBytes) {
        $requiredGb = [Math]::Ceiling($requiredBytes / 1GB)
        $freeGb = [Math]::Floor($drive.AvailableFreeSpace / 1GB)
        throw "目标磁盘空间不足：需要约 $requiredGb GB，可用 $freeGb GB。请更换 -TargetDir。"
    }
    Write-Info ("目标磁盘可用空间：{0:N1} GB；本次最低预留：{1:N1} GB" -f ($drive.AvailableFreeSpace / 1GB), ($requiredBytes / 1GB))

    $Step = '3/8 Python 3.8 x64'
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    $CreatedTarget = $true
    $useVenv = $true
    $python = $null
    if ($Portable) {
        Write-Info '按 -Portable 使用包内便携运行时。'
        $python = Install-PortableRuntime $TargetDir
        $useVenv = $false
    } else {
        $python = Resolve-Py38
        if (-not $python) {
            Write-Info '未找到可用的 Python 3.8 x64，尝试包内安装程序。'
            $setup = Start-Process -FilePath $installer -ArgumentList '/quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_launcher=1 Include_test=0' -PassThru -Wait -WindowStyle Hidden
            Write-Info "Python 安装器退出码：$($setup.ExitCode)"
            $python = Resolve-Py38
        }
        if (-not $python) {
            Write-Info '安装器未能提供可用解释器，自动回退到包内便携运行时。'
            $python = Install-PortableRuntime $TargetDir
            $useVenv = $false
        }
    }
    if (-not (Test-Py38 $python)) { throw 'Python 解释器版本或架构不符合要求。' }
    Write-Info "使用 Python：$python"

    $Step = '4/8 解压源码与安装依赖'
    $sourceStage = Join-Path $env:TEMP ("AAS_Source_{0}" -f [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $sourceStage | Out-Null
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $sourceStage -Force
    $sourceRoot = Join-Path $sourceStage 'AAS'
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'scripts\visualization_server.py'))) {
        throw '源码 ZIP 结构无效或缺少服务入口。'
    }
    Get-ChildItem -LiteralPath $sourceRoot -Force | ForEach-Object {
        Move-Item -LiteralPath $_.FullName -Destination $TargetDir -Force
    }
    Remove-Item -LiteralPath $sourceStage -Recurse -Force

    if ($useVenv) {
        $venv = Join-Path $TargetDir '.venv'
        & $python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
        $runtimePython = Join-Path $venv 'Scripts\python.exe'
    } else {
        $runtimePython = $python
    }
    & $runtimePython -m pip install --no-index --find-links $wheelDir -r $lockFile --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw '离线依赖安装失败；请检查 wheelhouse 与 Python ABI。' }
    & $runtimePython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'pip check 未通过，依赖存在冲突。' }

    $Step = '5/8 初始化模型目录'
    $modelsDir = Join-Path $TargetDir 'models'
    New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
    Write-Info '本离线包不预置实验模型；请使用自己的数据训练并发布模型。'

    $Step = '6/8 安装示例数据或迁移数据库'
    $datasetsDir = Join-Path $TargetDir 'datasets'
    New-Item -ItemType Directory -Force -Path $datasetsDir | Out-Null
    $db = Join-Path $datasetsDir 'aas_telemetry_v2.sqlite'
    if ($DatabaseSource) {
        if (Test-Path -LiteralPath $db) { throw "目标 V2 数据库已存在，拒绝覆盖：$db" }
        Copy-Item -LiteralPath (Resolve-Path -LiteralPath $DatabaseSource).Path -Destination $db
        $backup = Join-Path $datasetsDir ("aas_telemetry_v2.before_migration_{0}.sqlite" -f $Stamp)
        Copy-Item -LiteralPath $db -Destination $backup
        Push-Location $TargetDir
        try {
            & $runtimePython -m scripts.migrate_v2_coordinates --db $db --apply
            if ($LASTEXITCODE -ne 0) { throw "数据库迁移失败；副本备份位于 $backup" }
        } finally { Pop-Location }
    } elseif (-not $SkipDatabase) {
        $rawSource = Join-Path $Pkg '数据\遥测'
        $labelSource = Join-Path $Pkg '数据\标签\targets.zip'
        $rawFiles = @(Get-ChildItem -LiteralPath $rawSource -Filter '*.csv.gz' -File -ErrorAction SilentlyContinue)
        if ($rawFiles.Count -gt 0 -and (Test-Path -LiteralPath $labelSource)) {
            $rawTarget = Join-Path $datasetsDir 'source_csv_gz'
            $labelTarget = Join-Path $datasetsDir 'source_labels'
            New-Item -ItemType Directory -Force -Path $rawTarget, $labelTarget | Out-Null
            Copy-Item -LiteralPath $rawFiles.FullName -Destination $rawTarget -Force
            Copy-Item -LiteralPath $labelSource -Destination (Join-Path $labelTarget 'targets.zip') -Force
            Push-Location $TargetDir
            try {
                & $runtimePython -m scripts.rebuild_target_labeled_v2
                if ($LASTEXITCODE -ne 0) { throw '从示例 CSV.GZ 和标签构建 V2 数据库失败。' }
            } finally { Pop-Location }
        } elseif ($rawFiles.Count -eq 0 -and -not (Test-Path -LiteralPath $labelSource)) {
            Push-Location $TargetDir
            try {
                & $runtimePython -m scripts.create_empty_v2 --out $db
                if ($LASTEXITCODE -ne 0) { throw '创建空 V2 数据库失败。' }
            } finally { Pop-Location }
        } else {
            throw '示例遥测 CSV.GZ 与 targets.zip 不完整，拒绝创建不完整数据库。'
        }
    } else {
        Write-Info '按 -SkipDatabase 跳过数据库创建。'
    }

    $Step = '7/8 数据库和依赖自检'
    & $runtimePython -m pip check
    if ($LASTEXITCODE -ne 0) { throw '最终 pip check 未通过。' }
    if (-not $SkipDatabase) {
        Push-Location $TargetDir
        try {
            & $runtimePython -m scripts.check_dual_coordinate_v2 --db $db
            if ($LASTEXITCODE -ne 0) { throw 'V2 数据库自检失败。' }
        } finally { Pop-Location }
    }

    $Step = '8/8 写入安装工具'
    foreach ($name in @('Start-AAS.ps1','Stop-AAS.ps1','Uninstall-AAS.ps1','Start-AAS.cmd','Stop-AAS.cmd','Uninstall-AAS.cmd')) {
        $from = Join-Path $Pkg $name
        if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "离线包缺少安装后工具：$name" }
        Copy-Item -LiteralPath $from -Destination (Join-Path $TargetDir $name) -Force
    }
    $installInfo = [ordered]@{
        product = 'AAS'
        source_commit = $manifest.source_commit
        installed_utc = (Get-Date).ToUniversalTime().ToString('o')
        python = $runtimePython
        port = 8765
        package_manifest = '校验/双坐标文件哈希.json'
    }
    $installInfo | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $TargetDir '.aas-install.json') -Encoding UTF8

    Write-Host ''
    Write-Host '================ AAS 部署完成 ================' -ForegroundColor Green
    Write-Host "安装目录：$TargetDir"
    Write-Host '启动服务：双击安装目录中的 Start-AAS.cmd'
    Write-Host '停止服务：双击安装目录中的 Stop-AAS.cmd'
    Write-Host "部署日志：$LogPath"
    if ($StartAfterDeploy) {
        & (Join-Path $TargetDir 'Start-AAS.ps1') -OpenBrowser
    } else {
        Write-Info '服务尚未启动；请使用 Start-AAS.cmd。'
    }
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    Write-Host ''
    Write-Host ("[FAIL] 步骤：{0}" -f $Step) -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($CreatedTarget -and (Test-Path -LiteralPath $TargetDir)) {
        Write-Host '清理本次新建的失败安装目录；不会触碰原数据库来源。' -ForegroundColor Yellow
        try { Remove-Item -LiteralPath $TargetDir -Recurse -Force } catch {
            Write-Host ("自动清理失败，请手工检查：{0}" -f $TargetDir) -ForegroundColor Yellow
        }
    }
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host "部署日志：$LogPath" -ForegroundColor Yellow
    exit 1
}
