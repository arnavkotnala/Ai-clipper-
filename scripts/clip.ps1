<#
.SYNOPSIS
  Offline clipper: one video file in, 9:16 Shorts + a measured report out.
      powershell -ExecutionPolicy Bypass -File scripts\clip.ps1 "D:\videos\my-match.mp4"
      powershell -ExecutionPolicy Bypass -File scripts\clip.ps1 "D:\videos\my-match.mp4" -Truth my-highlights.txt -Top 8

.DESCRIPTION
  Runs everything locally: audio -> Whisper transcript -> highlight signals -> vision check ->
  facecam detection -> 9:16 render with subtitles (NVENC). Watches every network connection while
  it runs and says whether the run stayed offline. Writes to exports\<project-id>\:
  the clips, report.md (read this) and report.json.

  -Truth file   your own list of real highlights, one per line, written BEFORE you look at the
                results:   01:12-01:25, multi_kill, triple kill with AWP
                Then the report shows correct / false positives / missed / timing error, for
                plain SharkyClips (baseline) and for ai-clipper side by side.
  -Top N        how many candidates to look for (default ~1 per 4 minutes of video)
  -NoVision     skip the vision check (faster; no Ollama needed)
  -Subs style   bold (default), clean, minimal, none
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Video,
    [string]$Truth,
    [int]$Top = 0,
    [switch]$NoVision,
    [ValidateSet("bold", "clean", "minimal", "none")][string]$Subs = "bold"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup.ps1 first." -ForegroundColor Yellow; exit 1 }
$Video = (Resolve-Path -LiteralPath $Video).Path
$env:PATH = (Join-Path $Root "tools\ffmpeg\bin") + ";" + $env:PATH
$env:PYTHONIOENCODING = "utf-8"

$cliArgs = @("cli.py", "clip", $Video, "--subs", $Subs)
if ($Truth) { $cliArgs += @("--truth", (Resolve-Path -LiteralPath $Truth).Path) }
if ($Top -gt 0) { $cliArgs += @("--top", "$Top") }
if ($NoVision) { $cliArgs += "--no-vision" }

Push-Location (Join-Path $Root "app\backend")
try {
    & $Py @cliArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -eq 2) { Write-Host "WARNING: the run made a network connection (see report.md)." -ForegroundColor Yellow }
exit $code
