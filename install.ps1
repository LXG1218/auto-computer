[CmdletBinding()]
param(
    [ValidateSet('all', 'antigravity', 'codex', 'none')]
    [string]$Target = 'all',
    [string]$HomeDir = $(if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'AutoComputer' } else { Join-Path $HOME 'AutoComputer' }),
    [switch]$SkipNode,
    [switch]$SkipOcr,
    [switch]$SkipMcpConfig
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Resolve-BasePython {
    if ($env:AUTO_COMPUTER_PYTHON -and (Test-Path -LiteralPath $env:AUTO_COMPUTER_PYTHON)) {
        return @($env:AUTO_COMPUTER_PYTHON)
    }
    $cmdPy = Get-Command python -ErrorAction SilentlyContinue
    if ($cmdPy -and $cmdPy.Source -notmatch 'WindowsApps\\python\.exe$') {
        return @($cmdPy.Source)
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @($pyLauncher.Source, '-3')
    }
    throw "Python 3.10+ not found. Please install Python 3.10+ and re-run .\install.ps1."
}

function Copy-WithBackup([string]$Src, [string]$Dst, [string]$BackupDir) {
    if (Test-Path -LiteralPath $Dst) {
        New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
        $leaf = Split-Path -Leaf $Dst
        Copy-Item -LiteralPath $Dst -Destination (Join-Path $BackupDir $leaf) -Recurse -Force
    }
    $parent = Split-Path -Parent $Dst
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Src -Destination $Dst -Recurse -Force
}

$repoRoot = $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $HomeDir "backups\install-$stamp"

Write-Host "=== Installing Auto Computer into $HomeDir ==="
New-Item -ItemType Directory -Force -Path $HomeDir, (Join-Path $HomeDir 'mcp'), (Join-Path $HomeDir 'tools'), (Join-Path $HomeDir 'profiles'), (Join-Path $HomeDir 'secrets'), (Join-Path $HomeDir 'backups') | Out-Null

# 1. Python venv & packages
$venvDir = Join-Path $HomeDir '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) {
    $basePy = Resolve-BasePython
    Write-Host "[1/5] Creating Python virtual environment at $venvDir ..."
    if ($basePy.Count -gt 1) {
        & $basePy[0] $basePy[1] -m venv $venvDir
    } else {
        & $basePy[0] -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python venv." }
} else {
    Write-Host "[1/5] Reusing existing Python venv at $venvDir"
}

Write-Host "[2/5] Installing Python dependencies..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $repoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "Failed to install core Python requirements." }

if (-not $SkipOcr) {
    Write-Host "      Installing optional WinRT OCR dependencies..."
    & $venvPy -m pip install -r (Join-Path $repoRoot 'requirements-ocr.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "WinRT OCR packages failed to install; window_ocr.py will fall back to PowerShell local_ocr.ps1."
    }
}

# 2. Node.js MCP packages
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) { $npmCmd = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $SkipNode) {
    if ($nodeCmd -and $npmCmd) {
        Write-Host "[3/5] Installing Node.js browser tools (@playwright/cli & chrome-devtools-mcp)..."
        $pwDir = Join-Path $HomeDir 'tools\playwright'
        $cdDir = Join-Path $HomeDir 'tools\chrome-devtools-mcp'
        New-Item -ItemType Directory -Force -Path $pwDir, $cdDir | Out-Null
        if (-not (Test-Path -LiteralPath (Join-Path $pwDir 'package.json'))) {
            [System.IO.File]::WriteAllText((Join-Path $pwDir 'package.json'), '{"private":true}', $utf8NoBom)
        }
        if (-not (Test-Path -LiteralPath (Join-Path $cdDir 'package.json'))) {
            [System.IO.File]::WriteAllText((Join-Path $cdDir 'package.json'), '{"private":true}', $utf8NoBom)
        }
        & $npmCmd.Source --prefix $pwDir install --no-audit --no-fund @playwright/cli
        & $npmCmd.Source --prefix $cdDir install --no-audit --no-fund chrome-devtools-mcp
    } else {
        Write-Warning "Node.js / npm not found. Browser MCP tools will require Node.js 20+ (rerun install.ps1 after installing Node.js)."
    }
} else {
    Write-Host "[3/5] Skipping Node.js packages (-SkipNode)."
}

# 3. Stage MCP servers & skills
Write-Host "[4/5] Staging MCP servers and skills..."
$mcpDest = Join-Path $HomeDir 'mcp'
Copy-Item -LiteralPath (Join-Path $repoRoot 'mcp\desktop_mcp.py') -Destination (Join-Path $mcpDest 'desktop_mcp.py') -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'mcp\edge_mcp.cjs') -Destination (Join-Path $mcpDest 'edge_mcp.cjs') -Force

$skillSrc = Join-Path $repoRoot 'skill\auto-computer'
$antigravitySkillDir = Join-Path $env:USERPROFILE '.gemini\config\skills\auto-computer'
$codexSkillDir = Join-Path $env:USERPROFILE '.codex\skills\auto-computer'

if ($Target -in @('all', 'antigravity')) {
    Copy-WithBackup $skillSrc $antigravitySkillDir (Join-Path $backupRoot 'antigravity-skill')
    Write-Host "      Installed skill -> $antigravitySkillDir"
}
if ($Target -in @('all', 'codex')) {
    Copy-WithBackup $skillSrc $codexSkillDir (Join-Path $backupRoot 'codex-skill')
    Write-Host "      Installed skill -> $codexSkillDir"
}

# 4. Merge Antigravity MCP config
if (-not $SkipMcpConfig -and $Target -in @('all', 'antigravity')) {
    $mcpConfigPath = Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $mcpConfigPath) | Out-Null
    $configObj = @{ mcpServers = @{} }
    if (Test-Path -LiteralPath $mcpConfigPath) {
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        Copy-Item -LiteralPath $mcpConfigPath -Destination (Join-Path $backupRoot 'mcp_config.json') -Force
        $rawCfg = [System.IO.File]::ReadAllText($mcpConfigPath, $utf8NoBom)
        if ($rawCfg.Trim()) {
            $parsed = $rawCfg | ConvertFrom-Json
            $serversTable = @{}
            if ($parsed.mcpServers) {
                foreach ($prop in $parsed.mcpServers.PSObject.Properties) {
                    $serversTable[$prop.Name] = $prop.Value
                }
            }
            $configObj = @{ mcpServers = $serversTable }
        }
    }

    $nodeExec = if ($nodeCmd) { $nodeCmd.Source } else { 'node' }
    $uiaScriptPath = Join-Path $antigravitySkillDir 'scripts\win_uia.ps1'
    $chromeMcpEntry = Join-Path $HomeDir 'tools\chrome-devtools-mcp\node_modules\chrome-devtools-mcp\out\Default\gen\src\bin\chrome-devtools-mcp.js'

    $configObj.mcpServers['edge-devtools'] = @{
        command = $nodeExec
        args    = @((Join-Path $mcpDest 'edge_mcp.cjs'))
        env     = @{ AUTO_COMPUTER_HOME = $HomeDir }
    }
    $configObj.mcpServers['windows-uia'] = @{
        command = $venvPy
        args    = @('-X', 'utf8', (Join-Path $mcpDest 'desktop_mcp.py'))
        env     = @{
            AUTO_COMPUTER_HOME    = $HomeDir
            AUTO_COMPUTER_PYTHON  = $venvPy
            ANTIGRAVITY_UIA_SCRIPT = $uiaScriptPath
        }
    }
    $configObj.mcpServers['chrome-devtools'] = @{
        command = $nodeExec
        args    = @($chromeMcpEntry, '--autoConnect')
    }

    $jsonOut = $configObj | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($mcpConfigPath, $jsonOut, $utf8NoBom)
    Write-Host "      Updated MCP config -> $mcpConfigPath (backup in $backupRoot)"
}

Write-Host "[5/5] Running environment & authorization check..."
$env:AUTO_COMPUTER_HOME = $HomeDir
$checkScript = Join-Path $repoRoot 'skill\auto-computer\scripts\setup_auth.py'
& $venvPy -X utf8 $checkScript --check

Write-Host "`n安装完成！首次使用如需接管日常 Chrome/Edge 已登录页面，请运行："
Write-Host "  .\setup-auth.ps1"
Write-Host "之后请重载 Antigravity / Codex MCP 服务即可使用。"
