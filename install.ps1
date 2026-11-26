[CmdletBinding()]
param(
    [ValidateSet('all', 'antigravity', 'codex', 'generic', 'none')]
    [string]$Target = 'all',
    [string]$HomeDir = $(if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'AutoComputer' } else { Join-Path $HOME 'AutoComputer' }),
    [string]$PythonExe = $env:AUTO_COMPUTER_PYTHON,
    [string]$ToolsDir = $env:AUTO_COMPUTER_TOOLS_DIR,
    [switch]$InstallPythonDeps,
    [switch]$SkipNode,
    [switch]$SkipOcr,
    [switch]$SkipMcpConfig,
    [switch]$NoUserEnv
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Resolve-BasePython {
    if ($env:AUTO_COMPUTER_PYTHON -and (Test-Path -LiteralPath $env:AUTO_COMPUTER_PYTHON)) { return @($env:AUTO_COMPUTER_PYTHON) }
    $cmdPy = Get-Command python -ErrorAction SilentlyContinue
    if ($cmdPy -and $cmdPy.Source -notmatch 'WindowsApps\\python\.exe$') { return @($cmdPy.Source) }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return @($pyLauncher.Source, '-3') }
    throw 'Python 3.10+ not found. Install Python 3.10+ and rerun install.ps1.'
}

function Invoke-Python([string[]]$Prefix, [string[]]$PyArgs) {
    if ($Prefix.Count -gt 1) { & $Prefix[0] $Prefix[1] @PyArgs } else { & $Prefix[0] @PyArgs }
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $($Prefix -join ' ') $($PyArgs -join ' ')" }
}

function Save-ExistingFile([string]$Path, [string]$BackupBase, [string]$RelativePath) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $backupPath = Join-Path $BackupBase $RelativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupPath) | Out-Null
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
}

function Copy-TreePreservingExtras([string]$Source, [string]$Destination, [string]$BackupBase) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
    Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($sourceRoot.Length).TrimStart('\')
        $targetPath = Join-Path $Destination $relative
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Force -Path $targetPath | Out-Null
            return
        }
        if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
            $backupRelative = Join-Path (Split-Path -Leaf $Destination) $relative
            Save-ExistingFile $targetPath $BackupBase $backupRelative
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
    }
}

function ConvertTo-McpJson($Value) {
    return ($Value | ConvertTo-Json -Depth 30)
}

function Read-JsonObject([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return [ordered]@{} }
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    if (-not $raw.Trim()) { return [ordered]@{} }
    $parsed = ConvertFrom-Json -InputObject $raw
    if ($parsed -isnot [System.Management.Automation.PSCustomObject] -and $parsed -isnot [System.Collections.IDictionary]) { throw "Expected a JSON object in $Path" }
    return (Convert-JsonValue $parsed)
}

function Convert-JsonValue($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($key in $Value.Keys) { $result[$key] = Convert-JsonValue $Value[$key] }
        return $result
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $result = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) { $result[$property.Name] = Convert-JsonValue $property.Value }
        return $result
    }
    if ($Value -is [array]) { return ,@($Value | ForEach-Object { Convert-JsonValue $_ }) }
    return $Value
}

function New-ServerConfig([string]$NodeCommand, [string]$PythonExe, [string]$McpDir, [string]$SkillDir, [string]$AcHome, [string]$ToolsDir) {
    return [ordered]@{
        'edge-devtools' = [ordered]@{
            command = $NodeCommand
            args = @((Join-Path $McpDir 'edge_mcp.cjs'))
            env = [ordered]@{
                AUTO_COMPUTER_HOME = $AcHome
                AUTO_COMPUTER_PYTHON = $PythonExe
                AUTO_COMPUTER_TOOLS_DIR = $ToolsDir
                AUTO_COMPUTER_PROFILE_ROOT = (Join-Path $AcHome 'profiles')
                AUTO_COMPUTER_ADAPTERS_DIR = (Join-Path $AcHome 'adapters')
            }
        }
        'windows-uia' = [ordered]@{
            command = $PythonExe
            args = @('-X', 'utf8', (Join-Path $McpDir 'desktop_mcp.py'))
            env = [ordered]@{
                AUTO_COMPUTER_HOME = $AcHome
                AUTO_COMPUTER_PYTHON = $PythonExe
                AUTO_COMPUTER_ADAPTERS_DIR = (Join-Path $AcHome 'adapters')
                AUTO_COMPUTER_UIA_SCRIPT = (Join-Path $SkillDir 'scripts\win_uia.ps1')
            }
        }
    }
}

function Merge-JsonServers([string]$Path, $Servers, [string]$BackupBase, [string]$BackupName) {
    $root = Read-JsonObject $Path
    if ($root.Contains('mcpServers') -and $root.mcpServers -isnot [System.Collections.IDictionary]) { throw "Expected mcpServers to be an object in $Path" }
    $serverTable = if ($root.Contains('mcpServers')) { $root.mcpServers } else { [ordered]@{} }
    foreach ($name in $Servers.Keys) {
        $server = Convert-JsonValue $Servers[$name]
        if ($serverTable.Contains($name) -and $serverTable[$name] -is [System.Collections.IDictionary] -and $serverTable[$name].env -is [System.Collections.IDictionary]) {
            $mergedEnv = [ordered]@{}
            foreach ($key in $serverTable[$name].env.Keys) { $mergedEnv[$key] = $serverTable[$name].env[$key] }
            foreach ($key in $server.env.Keys) { $mergedEnv[$key] = $server.env[$key] }
            $server.env = $mergedEnv
        }
        $serverTable[$name] = $server
    }
    $root['mcpServers'] = $serverTable
    if (Test-Path -LiteralPath $Path -PathType Leaf) { Save-ExistingFile $Path $BackupBase $BackupName }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [System.IO.File]::WriteAllText($Path, (ConvertTo-McpJson $root), $utf8NoBom)
}

function Register-CodexMcp([string]$Name, [string]$Command, [string[]]$ServerArgs, [System.Collections.IDictionary]$Environment) {
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) { throw 'Codex CLI is required for -Target codex. Add codex to PATH, then rerun install.ps1.' }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $existing = & $codex.Source mcp get $Name --json 2>$null
        $getCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($getCode -eq 0) {
        try {
            $existingConfig = ($existing -join "`n") | ConvertFrom-Json
            $previousEnv = if ($existingConfig.transport.env) { $existingConfig.transport.env } else { $existingConfig.env }
            if ($previousEnv) {
                foreach ($property in $previousEnv.PSObject.Properties) {
                    if (-not $Environment.Contains($property.Name)) { $Environment[$property.Name] = [string]$property.Value }
                }
            }
        } catch { throw "Could not read existing Codex MCP environment for '$Name'; left its registration unchanged." }
        & $codex.Source mcp remove $Name
        if ($LASTEXITCODE -ne 0) { throw "Could not replace Codex MCP server '$Name'." }
    }
    $argv = @('mcp', 'add', $Name)
    foreach ($key in $Environment.Keys) { $argv += @('--env', "$key=$($Environment[$key])") }
    $argv += @('--', $Command)
    $argv += $ServerArgs
    & $codex.Source @argv
    if ($LASTEXITCODE -ne 0) { throw "Codex MCP registration failed for '$Name'." }
}

$repoRoot = $PSScriptRoot
$HomeDir = [System.IO.Path]::GetFullPath($HomeDir)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $HomeDir "backups\install-$stamp"
$skillSrc = Join-Path $repoRoot 'skill\auto-computer'
$skillDir = Join-Path $HomeDir 'skills\auto-computer'
$venvDir = Join-Path $HomeDir '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
$mcpDir = Join-Path $HomeDir 'mcp'
if (Test-Path -LiteralPath (Join-Path $HomeDir 'runtime.json') -PathType Leaf) {
    $runtimeState = Read-JsonObject (Join-Path $HomeDir 'runtime.json')
    if (-not $PythonExe -and $runtimeState.python) { $PythonExe = [string]$runtimeState.python }
    if (-not $ToolsDir -and $runtimeState.tools) { $ToolsDir = [string]$runtimeState.tools }
}
$toolsDir = if ($ToolsDir) { [System.IO.Path]::GetFullPath($ToolsDir) } else { Join-Path $HomeDir 'tools' }

Write-Host "=== Installing Auto Computer into $HomeDir ($Target) ==="
New-Item -ItemType Directory -Force -Path $HomeDir, $mcpDir, $toolsDir, (Join-Path $HomeDir 'profiles'), (Join-Path $HomeDir 'secrets'), (Join-Path $HomeDir 'adapters'), (Join-Path $HomeDir 'backups'), (Join-Path $HomeDir 'skills') | Out-Null

if ($PythonExe) {
    $venvPy = [System.IO.Path]::GetFullPath($PythonExe)
    if (-not (Test-Path -LiteralPath $venvPy -PathType Leaf)) { throw "Python interpreter not found: $venvPy" }
    Write-Host "[1/5] Using existing Python environment $venvPy"
} elseif (-not (Test-Path -LiteralPath $venvPy -PathType Leaf)) {
    $basePy = Resolve-BasePython
    Write-Host "[1/5] Creating Python environment at $venvDir ..."
    Invoke-Python $basePy @('-m', 'venv', $venvDir)
} else { Write-Host "[1/5] Reusing Python environment at $venvDir" }

if (-not $PythonExe -or $InstallPythonDeps) {
    Write-Host '[2/5] Updating Python dependencies...'
    Invoke-Python @($venvPy) @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')
    Invoke-Python @($venvPy) @('-m', 'pip', 'install', '-r', (Join-Path $repoRoot 'requirements.txt'))
    if (-not $SkipOcr) {
        & $venvPy -m pip install -r (Join-Path $repoRoot 'requirements-ocr.txt')
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Optional WinRT OCR packages failed; local PowerShell OCR remains available.' }
    }
} else { Write-Host '[2/5] Reusing existing Python packages (pass -InstallPythonDeps to install requirements).' }

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) { $npmCmd = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $SkipNode -and $nodeCmd -and $npmCmd) {
    Write-Host '[3/5] Updating optional Node browser packages...'
    $pwDir = Join-Path $toolsDir 'playwright'
    New-Item -ItemType Directory -Force -Path $pwDir | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $pwDir 'package.json'))) { [System.IO.File]::WriteAllText((Join-Path $pwDir 'package.json'), '{"private":true}', $utf8NoBom) }
    & $npmCmd.Source --prefix $pwDir install --no-audit --no-fund @playwright/cli
    if ($LASTEXITCODE -ne 0) { throw 'Could not install @playwright/cli.' }
} elseif ($SkipNode) { Write-Host '[3/5] Skipping Node packages (-SkipNode).' }
else { Write-Warning 'Node.js/npm not found; the Edge MCP will need Node.js. Desktop and skill CLI remain usable.' }

Write-Host '[4/5] Updating runtime, skill, and MCP configuration...'
foreach ($name in @('desktop_mcp.py', 'edge_mcp.cjs', 'foreground_guard.py')) {
    $dst = Join-Path $mcpDir $name
    Save-ExistingFile $dst $backupRoot (Join-Path 'mcp' $name)
    Copy-Item -LiteralPath (Join-Path $repoRoot (Join-Path 'mcp' $name)) -Destination $dst -Force
}
Copy-TreePreservingExtras $skillSrc $skillDir $backupRoot
$env:AUTO_COMPUTER_HOME = $HomeDir
$env:AUTO_COMPUTER_PYTHON = $venvPy
$env:AUTO_COMPUTER_TOOLS_DIR = $toolsDir
if (-not $NoUserEnv) {
    [Environment]::SetEnvironmentVariable('AUTO_COMPUTER_HOME', $HomeDir, 'User')
}

$runtimeState = [ordered]@{
    home = $HomeDir
    python = $venvPy
    tools = $toolsDir
    adapters = (Join-Path $HomeDir 'adapters')
    skill = $skillDir
    setup_auth = (Join-Path $HomeDir 'setup-auth.ps1')
    pair_browser = (Join-Path $HomeDir 'pair-browser.cmd')
}
[System.IO.File]::WriteAllText((Join-Path $HomeDir 'runtime.json'), (ConvertTo-McpJson $runtimeState), $utf8NoBom)

foreach ($supportFile in @('setup-auth.ps1', 'pair-browser.cmd')) {
    $sourceFile = Join-Path $repoRoot $supportFile
    if (Test-Path -LiteralPath $sourceFile -PathType Leaf) {
        $destinationFile = Join-Path $HomeDir $supportFile
        Save-ExistingFile $destinationFile $backupRoot (Join-Path 'runtime' $supportFile)
        Copy-Item -LiteralPath $sourceFile -Destination $destinationFile -Force
    }
}

$skillTargets = @()
if ($Target -in @('all', 'antigravity')) { $skillTargets += [pscustomobject]@{ Name='antigravity'; Path=(Join-Path $env:USERPROFILE '.gemini\config\skills\auto-computer') } }
if ($Target -in @('all', 'codex')) { $skillTargets += [pscustomobject]@{ Name='codex'; Path=(Join-Path $env:USERPROFILE '.codex\skills\auto-computer') } }
foreach ($targetSkill in $skillTargets) { Copy-TreePreservingExtras $skillSrc $targetSkill.Path $backupRoot }

$nodeExec = if ($nodeCmd) { $nodeCmd.Source } else { 'node' }
$servers = New-ServerConfig $nodeExec $venvPy $mcpDir $skillDir $HomeDir $toolsDir
if (-not $SkipMcpConfig -and $Target -in @('all', 'antigravity')) {
    $antigravityConfig = Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'
    Merge-JsonServers $antigravityConfig $servers $backupRoot 'config\antigravity-mcp_config.json'
    Write-Host "      Antigravity MCP config -> $antigravityConfig"
}
if (-not $SkipMcpConfig -and $Target -in @('all', 'generic')) {
    $genericConfig = Join-Path $HomeDir 'mcp-config.json'
    Merge-JsonServers $genericConfig $servers $backupRoot 'config\generic-mcp-config.json'
    Write-Host "      Generic stdio MCP config -> $genericConfig"
}
if (-not $SkipMcpConfig -and $Target -in @('all', 'codex')) {
    $hasCodex = [bool](Get-Command codex -ErrorAction SilentlyContinue)
    if ($Target -eq 'all' -and -not $hasCodex) {
        Write-Warning 'Codex CLI not found in PATH; skipped Codex MCP registration while keeping all other Auto Computer targets installed.'
    } else {
        foreach ($name in $servers.Keys) {
            $server = $servers[$name]
            Register-CodexMcp $name $server.command $server.args $server.env
        }
    }
}

Write-Host '[5/5] Checking installed Python environment...'
$checkScript = Join-Path $skillDir 'scripts\setup_auth.py'
Invoke-Python @($venvPy) @('-X','utf8',$checkScript,'--check')

Write-Host "`nInstall complete. Persistent machine dirs preserved (adapters, secrets, profiles, local-rules.md)."
Write-Host "Standalone skill dir: $skillDir"
if ($Target -in @('all','codex')) { Write-Host 'Codex MCP registered via codex mcp add.' }
if ($Target -in @('all','generic')) { Write-Host "Generic stdio MCP config: $(Join-Path $HomeDir 'mcp-config.json')" }
