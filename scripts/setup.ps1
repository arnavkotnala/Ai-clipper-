<#
.SYNOPSIS
  ai-clipper setup: builds everything the app needs INSIDE the ai-clipper folder.

.DESCRIPTION
  Run it once on a new PC, after copying the ai-clipper folder (or cloning the repo):
      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

  What it does (nothing outside ai-clipper\ is written, nothing needs admin rights):
    1. tools\uv      - the uv package manager (pinned version, sha256 checked)
    2. tools\python  - Python 3.12 (portable, managed by uv)
    3. .venv         - the Python environment, from app\backend\requirements.lock.txt
                       (rebuilt when it was copied from another PC: a venv stores absolute paths)
    4. tools\ffmpeg  - FFmpeg with NVENC/NVDEC (sha256 checked)
    5. Checks        - GPU, NVENC, CUDA for Whisper, which hardware profile was picked

  Options:
    -Models   also download the AI models into app\models (Whisper large-v3-turbo ~1.6 GB,
              vision model qwen3-vl:4b ~3.3 GB; needs Ollama installed). Already-present models
              are kept.
    -Dev      also install the frontend build tools (npm ci) for UI development.
    -Force    rebuild .venv even if it looks fine.
    -Offline  never download: use only what is already in tools\ (fails clearly if something is missing).

  Downloads come from: github.com/astral-sh/uv, the python-build-standalone mirror used by uv,
  pypi.org, github.com/FFmpeg-Builds, huggingface.co and ollama.com (only with -Models).
#>
param(
    [switch]$Models,
    [switch]$Dev,
    [switch]$Force,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is 10x slower with the progress bar
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Dl = Join-Path $Tools "_dl"
$Lock = Get-Content (Join-Path $PSScriptRoot "toolchain.lock.json") -Raw | ConvertFrom-Json
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Uv = Join-Path $Tools "uv\uv.exe"

$env:UV_CACHE_DIR = Join-Path $Tools "_uvcache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools "python"
$env:UV_NO_CONFIG = "1"
$env:UV_HTTP_TIMEOUT = "300"  # slow connections: uv's default (30 s) aborted a 21 MB Python download in testing
$env:UV_HTTP_RETRIES = "5"

function Write-Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Write-Note($text) { Write-Host "   --  $text" -ForegroundColor Yellow }

# Windows PowerShell 5.1 turns a native program's redirected stderr into errors, and
# "Stop" makes them fatal. uv and ffmpeg write progress to stderr, so redirect only through this.
function Invoke-Quiet([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block 2>&1 | Out-Null; return $LASTEXITCODE } finally { $ErrorActionPreference = $old }
}

function Get-Sha256([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLower() }

function Save-Verified([string]$Url, [string]$Sha256, [string]$Dest) {
    if ($Offline) { throw "Offline mode: $Dest is missing and may not be downloaded." }
    New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
    $part = "$Dest.part"
    Write-Host "   downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $part
    $got = Get-Sha256 $part
    if ($got -ne $Sha256.ToLower()) {
        Remove-Item -Force $part
        throw "Checksum mismatch for $Url`n  expected $Sha256`n  got      $got"
    }
    Move-Item -Force $part $Dest
}

function Expand-Into([string]$Zip, [string]$Dest) {
    $tmp = Join-Path $Dl ("x-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force
    $inner = Get-ChildItem $tmp
    $src = if ($inner.Count -eq 1 -and $inner[0].PSIsContainer) { $inner[0].FullName } else { $tmp }
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    Move-Item $src $Dest
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
}

# ---------------------------------------------------------------- 1. uv
Write-Step "1/5 uv $($Lock.uv.version)"
$uvOk = $false
if (Test-Path $Uv) { $uvOk = ((& $Uv --version) -match [regex]::Escape($Lock.uv.version)) }
if (-not $uvOk) {
    $zip = Join-Path $Dl "uv.zip"
    if (-not ((Test-Path $zip) -and (Get-Sha256 $zip) -eq $Lock.uv.sha256)) {
        Save-Verified $Lock.uv.url $Lock.uv.sha256 $zip
    }
    $tmp = Join-Path $Dl "uv-x"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force (Join-Path $Tools "uv") | Out-Null
    Get-ChildItem $tmp -Recurse -Filter "*.exe" | ForEach-Object { Copy-Item -Force $_.FullName (Join-Path $Tools "uv") }
    Remove-Item -Recurse -Force $tmp
}
Write-Ok (& $Uv --version)

# ---------------------------------------------------------------- 2. Python
Write-Step "2/5 Python $($Lock.python)"
$uvArgs = @()
if ($Offline) { $uvArgs += "--offline" }
# --no-bin: never put a python3.12.exe shim in the user .local folder (outside ai-clipper)
& $Uv python install $Lock.python --no-bin @uvArgs
if ($LASTEXITCODE -ne 0) { throw "uv could not install Python $($Lock.python)." }
Write-Ok "Python $($Lock.python) in tools\python"

# ---------------------------------------------------------------- 3. venv + packages
Write-Step "3/5 Python environment (.venv)"
$rebuild = $Force -or -not (Test-Path $Py)
$cfg = Join-Path $Venv "pyvenv.cfg"
if (-not $rebuild -and (Test-Path $cfg)) {
    $venvHome = (Select-String -Path $cfg -Pattern "^home\s*=\s*(.+)$").Matches[0].Groups[1].Value.Trim()
    if (-not $venvHome.StartsWith($Tools, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path $venvHome)) {
        Write-Note ".venv was made on another PC or folder ($venvHome): rebuilding it"
        $rebuild = $true
    }
}
if ($rebuild) {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    & $Uv venv $Venv --python $Lock.python @uvArgs
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed." }
}
$req = Join-Path $Root "app\backend\requirements.lock.txt"
# Try the local wheel cache first (works offline after a copy that included tools\_uvcache).
$code = Invoke-Quiet { & $Uv pip install --python $Py -r $req --offline }
if ($code -ne 0) {
    if ($Offline) { throw "Some packages are not in tools\_uvcache; run setup once with internet." }
    & $Uv pip install --python $Py -r $req
    if ($LASTEXITCODE -ne 0) { throw "Installing Python packages failed (see above)." }
}
Write-Ok "packages from requirements.lock.txt"

# ---------------------------------------------------------------- 4. FFmpeg
Write-Step "4/5 FFmpeg"
$ffDir = Join-Path $Tools "ffmpeg"
$ffExe = Join-Path $ffDir "bin\ffmpeg.exe"
$ffWorks = $false
if (Test-Path $ffExe) {
    try { $ffWorks = ((& $ffExe -hide_banner -version | Select-Object -First 1) -match "ffmpeg version") } catch { $ffWorks = $false }
}
if (-not $ffWorks) {
    $zip = Join-Path $Dl "ffmpeg.zip"
    $cachedOk = (Test-Path $zip) -and ($Lock.ffmpeg.cached_sha256 -contains (Get-Sha256 $zip))
    if (-not $cachedOk) {
        try {
            Save-Verified $Lock.ffmpeg.url $Lock.ffmpeg.sha256 $zip
        } catch {
            if ($Offline) { throw }
            Write-Note "The pinned FFmpeg build is gone (FFmpeg-Builds deletes old releases). Using today's $($Lock.ffmpeg.version_prefix) build instead."
            $rel = Invoke-RestMethod -UseBasicParsing -Uri $Lock.ffmpeg.latest_api -Headers @{ "User-Agent" = "ai-clipper-setup" }
            $asset = $rel.assets | Where-Object { $_.name -eq $Lock.ffmpeg.latest_asset } | Select-Object -First 1
            if (-not $asset -or -not $asset.digest) { throw "Could not find $($Lock.ffmpeg.latest_asset) with a published sha256." }
            Save-Verified $asset.browser_download_url ($asset.digest -replace "^sha256:", "") $zip
            Write-Note "Installed a newer FFmpeg build than the one tested; record it in docs\RESULTS.md."
        }
    }
    Expand-Into $zip $ffDir
}
$ffVersion = (& $ffExe -hide_banner -version | Select-Object -First 1)
Write-Ok $ffVersion

# ---------------------------------------------------------------- 5. checks
Write-Step "5/5 Hardware checks"
$env:PATH = (Join-Path $ffDir "bin") + ";" + $env:PATH
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | Select-Object -First 1)
    Write-Ok "GPU: $gpu"
    $code = Invoke-Quiet { & $ffExe -hide_banner -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v h264_nvenc -f null - }
    if ($code -eq 0) { Write-Ok "NVENC test encode works" } else { Write-Note "NVENC test encode failed: rendering will use the CPU (libx264)." }
} else {
    Write-Note "No NVIDIA GPU found (nvidia-smi missing): the CPU-only profile will be used."
}
$check = @'
import json, aiclipper_env
aiclipper_env.apply()
from sharkyclips import hwprofile
s = hwprofile.summary()
out = {"profile": s["name"], "file": s["file"], "load_errors": s["load_errors"]}
try:
    import ctranslate2
    out["cuda_devices_for_whisper"] = ctranslate2.get_cuda_device_count()
except Exception as e:  # noqa: BLE001
    out["cuda_devices_for_whisper"] = f"error: {e}"
print(json.dumps(out))
'@
Push-Location (Join-Path $Root "app\backend")
try { $info = ($check | & $Py - | Select-Object -Last 1) | ConvertFrom-Json } finally { Pop-Location }
Write-Ok "hardware profile: $($info.profile)  ($($info.file))"
if (@($info.load_errors.PSObject.Properties).Count -gt 0) { Write-Note "profile files with errors: $($info.load_errors | ConvertTo-Json -Compress)" }
Write-Ok "CUDA devices visible to Whisper: $($info.cuda_devices_for_whisper)"

# ---------------------------------------------------------------- optional: models
if ($Models) {
    Write-Step "Models (app\models)"
    $env:HF_HOME = Join-Path $Root "app\models\huggingface"
    $env:HF_HUB_DISABLE_TELEMETRY = "1"
    $whisper = @"
from faster_whisper.utils import download_model
print(download_model("$($Lock.whisper_model)"))
"@
    $whisper | & $Py -
    if ($LASTEXITCODE -ne 0) { throw "Whisper model download failed." }
    Write-Ok "Whisper $($Lock.whisper_model)"

    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (-not (Test-Path $ollama)) {
        Write-Note "Ollama is not installed: the vision check (Stage C) stays off. Install it from https://ollama.com, then run setup.ps1 -Models again."
    } else {
        $env:OLLAMA_MODELS = Join-Path $Root "app\models\ollama"
        $env:OLLAMA_HOST = "127.0.0.1:11435"
        $server = Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden -PassThru
        try {
            Start-Sleep -Seconds 3
            & $ollama pull $Lock.ollama_model
            if ($LASTEXITCODE -ne 0) { throw "ollama pull $($Lock.ollama_model) failed." }
            Write-Ok "vision model $($Lock.ollama_model) in app\models\ollama"
        } finally {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------- optional: dev tools
if ($Dev) {
    Write-Step "Frontend build tools (npm ci)"
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { Write-Note "Node.js is not installed: skip (the built UI in app\frontend\dist works without it)." }
    else {
        Push-Location (Join-Path $Root "app\frontend")
        try {
            $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
        } finally { Pop-Location }
        Write-Ok "npm ci"
    }
}

Write-Host ""
Write-Host "Setup finished. Start the app with:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1" -ForegroundColor Green
