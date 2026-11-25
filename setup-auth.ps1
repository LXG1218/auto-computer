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
$venvPy = Join-Path $acHome '.venv\Scripts\python.exe'
$candidates = @($env:AUTO_COMPUTER_PYTHON, $venvPy)
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

$scriptPath = Join-Path $PSScriptRoot 'skill\auto-computer\scripts\setup_auth.py'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    Write-Error "setup_auth.py not found: $scriptPath"
    exit 1
}

& $pythonExe -X utf8 $scriptPath @ForwardArgs
exit $LASTEXITCODE
