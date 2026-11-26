[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardArgs
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'AutoComputer' } else { Join-Path $HOME 'AutoComputer' }
$runtimePath = Join-Path $acHome 'runtime.json'
$rtPython = $null
if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
    try {
        $rt = (Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8) | ConvertFrom-Json
        if ($rt.python) { $rtPython = [string]$rt.python }
        if (-not $env:AUTO_COMPUTER_TOOLS_DIR -and $rt.tools) { $env:AUTO_COMPUTER_TOOLS_DIR = [string]$rt.tools }
    } catch { }
}

$venvPy = Join-Path $acHome '.venv\Scripts\python.exe'
$candidates = @($env:AUTO_COMPUTER_PYTHON, $rtPython, $venvPy)
$pythonExe = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if (-not $pythonExe) {
    $cmdPy = Get-Command python -ErrorAction SilentlyContinue
    if ($cmdPy -and $cmdPy.Source -notmatch 'WindowsApps\\python\.exe$') {
        $pythonExe = $cmdPy.Source
    }
}

if (-not $pythonExe) {
    Write-Error "Python environment not found. Please run .\install.ps1 first."
    exit 1
}

$scriptCandidates = @(
    (Join-Path $PSScriptRoot 'skill\auto-computer\scripts\setup_auth.py'),
    (Join-Path $PSScriptRoot 'skills\auto-computer\scripts\setup_auth.py')
)
$scriptPath = $scriptCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $scriptPath) {
    Write-Error "setup_auth.py not found under $PSScriptRoot"
    exit 1
}

& $pythonExe -X utf8 $scriptPath @ForwardArgs
exit $LASTEXITCODE
