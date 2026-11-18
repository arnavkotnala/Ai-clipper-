<#
.SYNOPSIS
  Start ai-clipper:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1

.DESCRIPTION
  Starts the web UI on http://127.0.0.1:8100 (this PC only) and opens it in the browser.
  The app starts its own private Ollama server (port 11435, models in app\models\ollama) only
  when the vision check needs it, and stops using the GPU between jobs.

  Options:
    -Port 8100     another port
    -NoBrowser     don't open the browser
    -Lan           also allow other devices on your Wi-Fi (asks them for a PIN)
#>
param(
    [int]$Port = 8100,
    [switch]$NoBrowser,
    [switch]$Lan
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "ai-clipper is not set up on this PC yet. Run first:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
    exit 1
}
$env:PATH = (Join-Path $Root "tools\ffmpeg\bin") + ";" + $env:PATH
$env:PYTHONIOENCODING = "utf-8"

$cliArgs = @("cli.py", "serve", "--port", "$Port")
if ($NoBrowser) { $cliArgs += "--no-browser" }
if ($Lan) { $cliArgs += "--lan" }

Push-Location (Join-Path $Root "app\backend")
try {
    & $Py @cliArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
