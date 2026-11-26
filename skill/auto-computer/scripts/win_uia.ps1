param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('windows', 'inspect', 'batch')]
    [string]$Mode,
    [switch]$IncludeUntitled,
    [string]$Process = '',
    [string]$Hwnd = '',
    [int]$MaxDepth = 5,
    [int]$Limit = 50,
    [string]$Name = '',
    [string]$AutomationId = '',
    [string]$ControlType = '',
    [string]$ActionsFile = '',
    [switch]$AllowForeground
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'AutoComputer' } else { Join-Path $HOME 'AutoComputer' }
$pythonCandidates = @(
    $env:AUTO_COMPUTER_PYTHON,
    (Join-Path $acHome '.venv\Scripts\python.exe')
)
$pythonExe = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $pythonExe) {
    $cmdPy = Get-Command python -ErrorAction SilentlyContinue
    if ($cmdPy) { $pythonExe = $cmdPy.Source }
}
if (-not $pythonExe -or -not (Test-Path -LiteralPath $pythonExe)) {
    Write-Output (@{ ok = $false; error = "Python interpreter not found; set AUTO_COMPUTER_PYTHON or run install.ps1" } | ConvertTo-Json -Compress)
    exit 1
}

$workerPy = Join-Path $PSScriptRoot 'win_uia_worker.py'
if (-not (Test-Path -LiteralPath $workerPy -PathType Leaf)) {
    Write-Output (@{ ok = $false; error = "UIA worker script missing: $workerPy" } | ConvertTo-Json -Compress)
    exit 1
}

$env:WIN_UIA_SCRIPT_DIR = $PSScriptRoot
$payload = @{
    mode            = $Mode
    includeUntitled = [bool]$IncludeUntitled
    process         = $Process
    hwnd            = $Hwnd
    maxDepth        = $MaxDepth
    limit           = $Limit
    name            = $Name
    automationId    = $AutomationId
    controlType     = $ControlType
    actionsFile     = $ActionsFile
    allowForeground = [bool]$AllowForeground
}
$env:WIN_UIA_ARGS = ($payload | ConvertTo-Json -Compress -Depth 4)

& $pythonExe -X utf8 $workerPy
exit $LASTEXITCODE
