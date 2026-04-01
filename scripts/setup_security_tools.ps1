#Requires -Version 5.1
<#
.SYNOPSIS
    AuditAI 安全工具一键安装脚本 (Windows 增强版)

.DESCRIPTION
    自动安装沙盒和外部安全扫描工具：
    - Python 工具: semgrep, bandit, safety
    - 系统工具: gitleaks, osv-scanner, trufflehog
    - Docker 沙盒镜像

    特性:
    - 多种安装方式自动回退
    - 网络问题自动重试
    - 详细的错误诊断
    - 支持代理设置
    - 虚拟环境兼容

.EXAMPLE
    .\setup_security_tools.ps1
    .\setup_security_tools.ps1 -InstallAll
    .\setup_security_tools.ps1 -VerifyOnly
#>

[CmdletBinding()]
param(
    [switch]$InstallAll,
    [switch]$PythonOnly,
    [switch]$SystemOnly,
    [switch]$DockerOnly,
    [switch]$VerifyOnly,
    [switch]$IncludeOptional,
    [switch]$Verbose,
    [switch]$Help
)

# ============================================================
# 配置
# ============================================================

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# 版本配置
$GITLEAKS_VERSION = "8.18.4"
$OSV_SCANNER_VERSION = "1.8.3"
$TRUFFLEHOG_VERSION = "3.80.0"

# 重试配置
$MAX_RETRIES = 3
$RETRY_DELAY = 2

# 获取脚本目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# 工具安装目录
$ToolsDir = "$env:LOCALAPPDATA\AuditAI\tools"

# ============================================================
# 辅助函数
# ============================================================

function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Type = "Info"
    )

    switch ($Type) {
        "Success" { Write-Host "✓ $Message" -ForegroundColor Green }
        "Error"   { Write-Host "✗ $Message" -ForegroundColor Red }
        "Warning" { Write-Host "! $Message" -ForegroundColor Yellow }
        "Info"    { Write-Host "→ $Message" -ForegroundColor Cyan }
        "Debug"   { if ($script:VerboseMode) { Write-Host "  $Message" -ForegroundColor DarkGray } }
        "Header"  {
            Write-Host ""
            Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Blue
            Write-Host "  $Message" -ForegroundColor Blue
            Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Blue
            Write-Host ""
        }
    }
}

function Test-Command {
    param([string]$Command)
    $result = Get-Command -Name $Command -ErrorAction SilentlyContinue
    return [bool]$result
}

function Test-AdminPrivilege {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-SystemArchitecture {
    if ([Environment]::Is64BitOperatingSystem) {
        return "x64"
    }
    return "x86"
}

# 带重试的下载函数
function Download-WithRetry {
    param(
        [string]$Url,
        [string]$OutFile,
        [string]$Description
    )

    for ($attempt = 1; $attempt -le $MAX_RETRIES; $attempt++) {
        Write-ColorOutput "下载 $Description (尝试 $attempt/$MAX_RETRIES)..." "Info"

        try {
            # 设置 TLS 1.2
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

            $webClient = New-Object System.Net.WebClient

            # 支持代理
            if ($env:HTTP_PROXY) {
                $proxy = New-Object System.Net.WebProxy($env:HTTP_PROXY)
                $webClient.Proxy = $proxy
            }

            $webClient.DownloadFile($Url, $OutFile)
            Write-ColorOutput "$Description 下载成功" "Success"
            return $true
        }
        catch {
            Write-ColorOutput "下载失败: $_" "Warning"

            if ($attempt -lt $MAX_RETRIES) {
                Write-ColorOutput "${RETRY_DELAY}秒后重试..." "Info"
                Start-Sleep -Seconds $RETRY_DELAY
            }
        }
    }

    Write-ColorOutput "$Description 下载失败 (已重试 $MAX_RETRIES 次)" "Error"
    return $false
}

# 带重试的 pip 安装
function Install-PipPackageWithRetry {
    param([string]$Package)

    for ($attempt = 1; $attempt -le $MAX_RETRIES; $attempt++) {
        Write-ColorOutput "安装 $Package (尝试 $attempt/$MAX_RETRIES)..." "Info"

        try {
            # 尝试常规安装
            $result = & $script:PipCmd install $Package --quiet 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-ColorOutput "$Package 安装成功" "Success"
                return $true
            }

            # 尝试 --user 安装
            $result = & $script:PipCmd install $Package --user --quiet 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-ColorOutput "$Package 安装成功 (--user)" "Success"
                return $true
            }

            # 第一次失败后尝试升级 pip
            if ($attempt -eq 1) {
                Write-ColorOutput "升级 pip 后重试..." "Debug"
                & $script:PipCmd install --upgrade pip --quiet 2>&1 | Out-Null
            }
        }
        catch {
            Write-ColorOutput "安装错误: $_" "Debug"
        }

        Start-Sleep -Seconds $RETRY_DELAY
    }

    Write-ColorOutput "$Package 安装失败" "Error"
    return $false
}

# 检测 Python 环境
function Detect-PythonEnvironment {
    Write-ColorOutput "检测 Python 环境..." "Info"

    $script:PythonCmd = $null
    $script:PipCmd = $null

    # 检查虚拟环境
    if ($env:VIRTUAL_ENV) {
        Write-ColorOutput "检测到虚拟环境: $env:VIRTUAL_ENV" "Info"
        $script:PythonCmd = "python"
        $script:PipCmd = "pip"
    }
    # 检查 python
    elseif (Test-Command "python") {
        $script:PythonCmd = "python"

        # 检查是否是 Python 3
        $version = & python --version 2>&1
        if ($version -notmatch "Python 3") {
            Write-ColorOutput "需要 Python 3.x，当前: $version" "Warning"
        }

        if (Test-Command "pip") {
            $script:PipCmd = "pip"
        }
        else {
            $script:PipCmd = "python -m pip"
        }
    }
    # 检查 python3
    elseif (Test-Command "python3") {
        $script:PythonCmd = "python3"
        if (Test-Command "pip3") {
            $script:PipCmd = "pip3"
        }
        else {
            $script:PipCmd = "python3 -m pip"
        }
    }
    else {
        Write-ColorOutput "未找到 Python！请先安装 Python 3.8+" "Error"
        Write-ColorOutput "下载地址: https://www.python.org/downloads/" "Info"
        return $false
    }

    # 验证
    try {
        $version = & $script:PythonCmd --version 2>&1
        Write-ColorOutput "Python: $version" "Success"

        # 确保 pip 可用
        $pipVersion = & $script:PipCmd --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput "pip 不可用，尝试安装..." "Warning"
            & $script:PythonCmd -m ensurepip --upgrade 2>&1 | Out-Null
        }

        return $true
    }
    catch {
        Write-ColorOutput "Python 验证失败: $_" "Error"
        return $false
    }
}

# 添加到 PATH
function Add-ToPath {
    param([string]$Directory)

    # 当前会话
    if ($env:PATH -notlike "*$Directory*") {
        $env:PATH = "$Directory;$env:PATH"
    }

    # 持久化到用户 PATH
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath -notlike "*$Directory*") {
        [Environment]::SetEnvironmentVariable("PATH", "$Directory;$userPath", "User")
        Write-ColorOutput "已添加 $Directory 到用户 PATH" "Debug"
    }
}

# 确保工具目录存在
function Ensure-ToolsDirectory {
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
        Write-ColorOutput "创建工具目录: $ToolsDir" "Info"
    }
    Add-ToPath $ToolsDir
}

# ============================================================
# Python 工具安装
# ============================================================

function Install-PythonTools {
    Write-ColorOutput "安装 Python 安全工具" "Header"

    if (-not (Detect-PythonEnvironment)) {
        return $false
    }

    $tools = @("bandit", "safety")
    $failed = @()
    $installed = @()

    # Semgrep 单独处理
    Write-ColorOutput "检查 semgrep..." "Info"
    if (Test-Command "semgrep") {
        $version = semgrep --version 2>&1 | Select-Object -First 1
        Write-ColorOutput "semgrep 已安装: $version" "Success"
    }
    else {
        if (Install-PipPackageWithRetry "semgrep") {
            $installed += "semgrep"
        }
        else {
            $failed += "semgrep"
            Write-ColorOutput "semgrep pip 安装失败，可尝试其他方式" "Warning"
        }
    }

    # 安装其他工具
    foreach ($tool in $tools) {
        Write-ColorOutput "检查 $tool..." "Info"
        if (Test-Command $tool) {
            Write-ColorOutput "$tool 已安装" "Success"
        }
        else {
            if (Install-PipPackageWithRetry $tool) {
                $installed += $tool
            }
            else {
                $failed += $tool
            }
        }
    }

    # 可选: TruffleHog
    if ($script:InstallOptional) {
        if (-not (Test-Command "trufflehog")) {
            Write-ColorOutput "安装 trufflehog..." "Info"
            if (Install-PipPackageWithRetry "trufflehog") {
                $installed += "trufflehog"
            }
            else {
                $failed += "trufflehog"
            }
        }
    }

    # 报告结果
    Write-Host ""
    if ($installed.Count -gt 0) {
        Write-ColorOutput "已安装: $($installed -join ', ')" "Success"
    }
    if ($failed.Count -gt 0) {
        Write-ColorOutput "安装失败: $($failed -join ', ')" "Warning"
        return $false
    }

    return $true
}

# ============================================================
# 系统工具安装
# ============================================================

function Install-SystemTools {
    Write-ColorOutput "安装 Windows 系统工具" "Header"

    Ensure-ToolsDirectory

    $arch = Get-SystemArchitecture
    $failed = @()
    $installed = @()

    # ---- Gitleaks ----
    Write-ColorOutput "检查 gitleaks..." "Info"
    if (Test-Command "gitleaks") {
        Write-ColorOutput "gitleaks 已安装" "Success"
    }
    else {
        if (Install-Gitleaks) {
            $installed += "gitleaks"
        }
        else {
            $failed += "gitleaks"
        }
    }

    # ---- OSV-Scanner ----
    Write-ColorOutput "检查 osv-scanner..." "Info"
    if (Test-Command "osv-scanner") {
        Write-ColorOutput "osv-scanner 已安装" "Success"
    }
    else {
        if (Install-OsvScanner) {
            $installed += "osv-scanner"
        }
        else {
            $failed += "osv-scanner"
        }
    }

    # ---- TruffleHog (可选) ----
    if ($script:InstallOptional) {
        if (-not (Test-Command "trufflehog")) {
            Write-ColorOutput "安装 trufflehog (二进制)..." "Info"
            if (Install-TruffleHog) {
                $installed += "trufflehog"
            }
        }
    }

    # 报告结果
    Write-Host ""
    if ($installed.Count -gt 0) {
        Write-ColorOutput "已安装: $($installed -join ', ')" "Success"
    }
    if ($failed.Count -gt 0) {
        Write-ColorOutput "安装失败: $($failed -join ', ')" "Warning"
        return $false
    }

    return $true
}

function Install-Gitleaks {
    $arch = if ((Get-SystemArchitecture) -eq "x64") { "x64" } else { "x32" }
    $url = "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION/gitleaks_${GITLEAKS_VERSION}_windows_${arch}.zip"
    $zipFile = "$env:TEMP\gitleaks.zip"

    if (Download-WithRetry -Url $url -OutFile $zipFile -Description "Gitleaks") {
        try {
            Expand-Archive -Path $zipFile -DestinationPath $ToolsDir -Force
            Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
            Write-ColorOutput "Gitleaks 安装成功" "Success"
            return $true
        }
        catch {
            Write-ColorOutput "Gitleaks 解压失败: $_" "Error"
        }
    }
    return $false
}

function Install-OsvScanner {
    $arch = if ((Get-SystemArchitecture) -eq "x64") { "amd64" } else { "386" }
    $url = "https://github.com/google/osv-scanner/releases/download/v$OSV_SCANNER_VERSION/osv-scanner_windows_${arch}.exe"
    $exeFile = "$ToolsDir\osv-scanner.exe"

    if (Download-WithRetry -Url $url -OutFile $exeFile -Description "OSV-Scanner") {
        Write-ColorOutput "OSV-Scanner 安装成功" "Success"
        return $true
    }
    return $false
}

function Install-TruffleHog {
    $arch = if ((Get-SystemArchitecture) -eq "x64") { "amd64" } else { "386" }
    $url = "https://github.com/trufflesecurity/trufflehog/releases/download/v$TRUFFLEHOG_VERSION/trufflehog_${TRUFFLEHOG_VERSION}_windows_${arch}.tar.gz"
    $tarFile = "$env:TEMP\trufflehog.tar.gz"

    if (Download-WithRetry -Url $url -OutFile $tarFile -Description "TruffleHog") {
        try {
            # 使用 tar (Windows 10 1803+)
            tar -xzf $tarFile -C $ToolsDir 2>$null
            Remove-Item $tarFile -Force -ErrorAction SilentlyContinue
            Write-ColorOutput "TruffleHog 安装成功" "Success"
            return $true
        }
        catch {
            Write-ColorOutput "TruffleHog 解压失败 (需要 Windows 10 1803+)" "Warning"
        }
    }
    return $false
}

# ============================================================
# 包管理器安装 (备选)
# ============================================================

function Install-WithPackageManager {
    Write-ColorOutput "使用包管理器安装工具" "Header"

    $hasScoop = Test-Command "scoop"
    $hasWinget = Test-Command "winget"
    $hasChoco = Test-Command "choco"

    if (-not ($hasScoop -or $hasWinget -or $hasChoco)) {
        Write-ColorOutput "未检测到包管理器 (scoop/winget/chocolatey)" "Warning"

        $response = Read-Host "是否自动安装 Scoop (推荐)? [Y/n]"
        if ($response -ne 'n' -and $response -ne 'N') {
            try {
                Write-ColorOutput "安装 Scoop..." "Info"
                Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
                Invoke-RestMethod get.scoop.sh | Invoke-Expression
                $hasScoop = $true
                Write-ColorOutput "Scoop 安装成功" "Success"
            }
            catch {
                Write-ColorOutput "Scoop 安装失败: $_" "Error"
                return $false
            }
        }
        else {
            return $false
        }
    }

    # 使用 Scoop
    if ($hasScoop) {
        Write-ColorOutput "使用 Scoop 安装工具..." "Info"

        # 添加 bucket
        scoop bucket add extras 2>$null
        scoop bucket add main 2>$null

        $scoopTools = @("gitleaks", "python")
        foreach ($tool in $scoopTools) {
            Write-ColorOutput "scoop install $tool..." "Info"
            scoop install $tool 2>&1 | Out-Null
            if (Test-Command $tool) {
                Write-ColorOutput "$tool 安装成功" "Success"
            }
        }
    }
    # 使用 Winget
    elseif ($hasWinget) {
        Write-ColorOutput "使用 Winget 安装工具..." "Info"
        winget install --id=Gitleaks.Gitleaks -e --silent 2>&1 | Out-Null
    }
    # 使用 Chocolatey
    elseif ($hasChoco) {
        Write-ColorOutput "使用 Chocolatey 安装工具..." "Info"
        choco install gitleaks -y 2>&1 | Out-Null
    }

    return $true
}

# ============================================================
# Docker 沙盒安装
# ============================================================

function Install-DockerSandbox {
    Write-ColorOutput "配置 Docker 沙盒" "Header"

    # 检查 Docker
    if (-not (Test-Command "docker")) {
        Write-ColorOutput "Docker 未安装！" "Error"
        Write-ColorOutput "请安装 Docker Desktop: https://www.docker.com/products/docker-desktop/" "Info"
        return $false
    }

    # 检查 Docker 是否运行
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "Docker 未运行！请启动 Docker Desktop" "Error"

        # 尝试启动 Docker Desktop
        Write-ColorOutput "尝试启动 Docker Desktop..." "Info"
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue

        Write-ColorOutput "等待 Docker 启动 (最多 60 秒)..." "Info"
        for ($i = 1; $i -le 12; $i++) {
            Start-Sleep -Seconds 5
            $dockerInfo = docker info 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-ColorOutput "Docker 已启动" "Success"
                break
            }
            Write-Host "." -NoNewline
        }
        Write-Host ""

        if ($LASTEXITCODE -ne 0) {
            Write-ColorOutput "Docker 启动超时，请手动启动 Docker Desktop" "Error"
            return $false
        }
    }

    Write-ColorOutput "Docker 已运行" "Success"

    # 构建沙盒镜像
    $sandboxDir = Join-Path $ProjectRoot "docker\sandbox"
    $dockerfile = Join-Path $sandboxDir "Dockerfile"

    if (-not (Test-Path $dockerfile)) {
        Write-ColorOutput "创建沙盒 Dockerfile..." "Info"
        New-SandboxDockerfile -Path $sandboxDir
    }

    Write-ColorOutput "构建 AuditAI 沙盒镜像..." "Info"

    Push-Location $sandboxDir
    try {
        for ($attempt = 1; $attempt -le $MAX_RETRIES; $attempt++) {
            Write-ColorOutput "构建镜像 (尝试 $attempt/$MAX_RETRIES)..." "Info"

            docker build -t auditai-sandbox:latest -f Dockerfile . 2>&1

            if ($LASTEXITCODE -eq 0) {
                Write-ColorOutput "沙盒镜像构建成功: auditai-sandbox:latest" "Success"

                # 验证
                Write-ColorOutput "验证沙盒镜像..." "Info"
                docker run --rm auditai-sandbox:latest python3 --version
                Write-ColorOutput "Python 环境正常" "Success"

                return $true
            }

            Write-ColorOutput "构建失败，重试..." "Warning"
            Start-Sleep -Seconds $RETRY_DELAY
        }

        Write-ColorOutput "沙盒镜像构建失败" "Error"
        return $false
    }
    finally {
        Pop-Location
    }
}

function New-SandboxDockerfile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }

    $dockerfileContent = @'
# AuditAI 安全沙盒
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget netcat-openbsd dnsutils iputils-ping ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 sandbox \
    && useradd -u 1000 -g sandbox -m -s /bin/bash sandbox

RUN pip install --no-cache-dir \
    requests httpx aiohttp beautifulsoup4 lxml \
    pycryptodome paramiko pyjwt python-jose sqlparse

WORKDIR /workspace
RUN mkdir -p /workspace /tmp/sandbox \
    && chown -R sandbox:sandbox /workspace /tmp/sandbox

USER sandbox
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/sandbox
CMD ["/bin/bash"]
'@

    $dockerfilePath = Join-Path $Path "Dockerfile"
    Set-Content -Path $dockerfilePath -Value $dockerfileContent -Encoding UTF8
    Write-ColorOutput "已创建沙盒 Dockerfile" "Success"
}

# ============================================================
# 验证安装
# ============================================================

function Test-Installation {
    Write-ColorOutput "验证安装结果" "Header"

    $tools = @(
        @{ Name = "semgrep";     Desc = "Semgrep 静态分析" },
        @{ Name = "bandit";      Desc = "Bandit Python安全" },
        @{ Name = "safety";      Desc = "Safety 依赖漏洞" },
        @{ Name = "gitleaks";    Desc = "Gitleaks 密钥检测" },
        @{ Name = "osv-scanner"; Desc = "OSV-Scanner 漏洞" },
        @{ Name = "trufflehog";  Desc = "TruffleHog 密钥" },
        @{ Name = "npm";         Desc = "NPM Audit" },
        @{ Name = "docker";      Desc = "Docker" }
    )

    $installed = 0
    $total = $tools.Count

    Write-Host ""
    Write-Host ("{0,-18} {1,-12} {2,-30}" -f "工具", "状态", "版本")
    Write-Host ("─" * 60)

    foreach ($tool in $tools) {
        $name = $tool.Name

        if (Test-Command $name) {
            $version = ""
            try {
                switch ($name) {
                    "semgrep"     { $version = (semgrep --version 2>&1 | Select-Object -First 1) }
                    "bandit"      { $version = (bandit --version 2>&1 | Select-Object -First 1) }
                    "safety"      { $version = (safety --version 2>&1 | Select-Object -First 1) }
                    "gitleaks"    { $version = (gitleaks version 2>&1 | Select-Object -First 1) }
                    "osv-scanner" { $version = (osv-scanner --version 2>&1 | Select-Object -First 1) }
                    "trufflehog"  { $version = (trufflehog --version 2>&1 | Select-Object -First 1) }
                    "npm"         { $version = (npm --version 2>&1) }
                    "docker"      { $version = ((docker --version 2>&1) -split ' ')[2] }
                }
                $version = $version.ToString().Substring(0, [Math]::Min(28, $version.Length))
            }
            catch {
                $version = "已安装"
            }

            Write-Host ("{0,-18} " -f $name) -NoNewline
            Write-Host ("{0,-12} " -f "已安装") -ForegroundColor Green -NoNewline
            Write-Host $version
            $installed++
        }
        else {
            Write-Host ("{0,-18} " -f $name) -NoNewline
            Write-Host ("{0,-12} " -f "未安装") -ForegroundColor Yellow -NoNewline
            Write-Host "-"
        }
    }

    Write-Host ("─" * 60)
    Write-Host ""

    # Docker 沙盒检查
    if (Test-Command "docker") {
        $imageExists = docker image inspect auditai-sandbox:latest 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "Docker 沙盒镜像: auditai-sandbox:latest ✓" "Success"
        }
        else {
            Write-ColorOutput "Docker 沙盒镜像未构建" "Warning"
        }
    }

    Write-Host ""
    Write-ColorOutput "安装统计: $installed/$total 个工具可用" "Info"

    # PATH 提示
    if ($env:PATH -notlike "*$ToolsDir*") {
        Write-ColorOutput "请重启终端使 PATH 生效" "Warning"
    }

    if ($installed -ge 5) {
        Write-ColorOutput "核心安全工具已就绪！" "Success"
        return $true
    }
    else {
        Write-ColorOutput "部分工具未安装，某些功能可能受限" "Warning"
        return $false
    }
}

# ============================================================
# 更新环境配置
# ============================================================

function Update-EnvConfig {
    Write-ColorOutput "更新环境配置" "Header"

    $envFile = Join-Path $ProjectRoot "backend\.env"

    if (-not (Test-Path $envFile)) {
        Write-ColorOutput ".env 文件不存在，跳过配置更新" "Warning"
        return
    }

    $envContent = Get-Content $envFile -Raw -ErrorAction SilentlyContinue

    if ($envContent -match "SANDBOX_IMAGE") {
        Write-ColorOutput "沙盒配置已存在于 .env 文件中" "Info"
    }
    else {
        Write-ColorOutput "添加沙盒配置到 .env 文件..." "Info"

        $sandboxConfig = @"

# =============================================
# 沙盒配置 (自动添加)
# =============================================
SANDBOX_IMAGE=auditai-sandbox:latest
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_LIMIT=1.0
SANDBOX_TIMEOUT=60
SANDBOX_NETWORK_MODE=none
"@

        Add-Content -Path $envFile -Value $sandboxConfig
        Write-ColorOutput "沙盒配置已添加到 .env" "Success"
    }
}

# ============================================================
# 显示帮助
# ============================================================

function Show-Help {
    Write-Host @"

╔═══════════════════════════════════════════════════════════════╗
║     AuditAI 安全工具一键安装脚本 (Windows 增强版)          ║
╚═══════════════════════════════════════════════════════════════╝

用法:
    .\setup_security_tools.ps1 [选项]

选项:
    -InstallAll         全部安装 (推荐)
    -PythonOnly         仅安装 Python 工具 (pip)
    -SystemOnly         仅安装系统工具 (二进制)
    -DockerOnly         仅构建 Docker 沙盒
    -VerifyOnly         仅验证安装状态
    -IncludeOptional    包含可选工具 (TruffleHog)
    -Verbose            显示详细输出
    -Help               显示帮助信息

示例:
    .\setup_security_tools.ps1                    # 交互式安装
    .\setup_security_tools.ps1 -InstallAll        # 自动全部安装
    .\setup_security_tools.ps1 -InstallAll -IncludeOptional  # 全部 + 可选
    .\setup_security_tools.ps1 -VerifyOnly        # 仅检查状态

"@
}

# ============================================================
# 显示菜单
# ============================================================

function Show-Menu {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════════╗" -ForegroundColor Blue
    Write-Host "║                                                               ║" -ForegroundColor Blue
    Write-Host "║     🔐 AuditAI 安全工具一键安装脚本 (Windows 增强版)       ║" -ForegroundColor Blue
    Write-Host "║                                                               ║" -ForegroundColor Blue
    Write-Host "╚═══════════════════════════════════════════════════════════════╝" -ForegroundColor Blue
    Write-Host ""

    Write-Host "请选择要安装的组件:"
    Write-Host "  1) 全部安装 (推荐)"
    Write-Host "  2) 仅 Python 工具 (pip)"
    Write-Host "  3) 仅系统工具 (二进制下载)"
    Write-Host "  4) 使用包管理器安装 (Scoop/Winget)"
    Write-Host "  5) 仅 Docker 沙盒"
    Write-Host "  6) 仅验证安装状态"
    Write-Host "  7) 退出"
    Write-Host ""

    $choice = Read-Host "请输入选项 [1-7]"
    return $choice
}

# ============================================================
# 主函数
# ============================================================

function Main {
    # 设置全局变量
    $script:VerboseMode = $Verbose
    $script:InstallOptional = $IncludeOptional

    # 处理命令行参数
    if ($Help) {
        Show-Help
        return
    }

    if ($VerifyOnly) {
        Test-Installation
        return
    }

    if ($InstallAll) {
        Install-PythonTools
        Install-SystemTools
        Install-DockerSandbox
        Update-EnvConfig
        Test-Installation
        return
    }

    if ($PythonOnly) {
        Install-PythonTools
        Test-Installation
        return
    }

    if ($SystemOnly) {
        Install-SystemTools
        Test-Installation
        return
    }

    if ($DockerOnly) {
        Install-DockerSandbox
        Update-EnvConfig
        Test-Installation
        return
    }

    # 交互式模式
    $choice = Show-Menu

    switch ($choice) {
        "1" {
            Install-PythonTools
            Install-SystemTools
            Install-DockerSandbox
            Update-EnvConfig
            Test-Installation
        }
        "2" {
            Install-PythonTools
            Test-Installation
        }
        "3" {
            Install-SystemTools
            Test-Installation
        }
        "4" {
            Install-WithPackageManager
            Test-Installation
        }
        "5" {
            Install-DockerSandbox
            Update-EnvConfig
            Test-Installation
        }
        "6" {
            Test-Installation
        }
        "7" {
            Write-Host "退出"
            return
        }
        default {
            Write-ColorOutput "无效选项" "Error"
            return
        }
    }

    Write-ColorOutput "安装完成" "Header"
    Write-Host ""
    Write-Host "下一步操作:"
    Write-Host "  1. 重启终端使 PATH 生效"
    Write-Host "  2. 启动后端: cd backend && uvicorn app.main:app --reload"
    Write-Host "  3. 在 Agent 审计中测试工具"
    Write-Host ""
}

# 运行主函数
Main
