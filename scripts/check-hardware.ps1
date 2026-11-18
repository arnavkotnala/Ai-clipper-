<#
.SYNOPSIS
  Show what ai-clipper detects on this PC and which hardware profile it uses:
      powershell -ExecutionPolicy Bypass -File scripts\check-hardware.ps1

.DESCRIPTION
  Prints CPU, RAM, GPU/VRAM/driver, the chosen profile (app\config\profiles) and its settings,
  and runs two short GPU tests: a 1-second NVENC encode and CUDA visibility for Whisper.
  Use it on PC B (RTX 5060) to confirm the profile before trusting it; paste the output
  into docs\RESULTS.md. Writes nothing.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Ff = Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup.ps1 first." -ForegroundColor Yellow; exit 1 }

$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1)
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Write-Host "CPU : $($cpu.Name.Trim()) ($($cpu.NumberOfCores) cores / $($cpu.NumberOfLogicalProcessors) threads)"
Write-Host "RAM : $ram GB"
$disk = Get-PSDrive -Name ($Root.Substring(0, 1))
Write-Host ("Disk: {0:N0} GB free on {1}:" -f ($disk.Free / 1GB), $disk.Name)

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host ("GPU : " + (& nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader | Select-Object -First 1))
    if (Test-Path $Ff) {
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        & $Ff -hide_banner -f lavfi -i testsrc2=size=1080x1920:rate=30 -t 10 -c:v h264_nvenc -f null - 2>&1 | Out-Null
        $code = $LASTEXITCODE; $sw.Stop(); $ErrorActionPreference = $old
        if ($code -eq 0) { Write-Host ("NVENC: OK (10 s of 1080x1920 in {0:N2} s)" -f $sw.Elapsed.TotalSeconds) }
        else { Write-Host "NVENC: FAILED (rendering falls back to the CPU)" -ForegroundColor Yellow }
    }
} else {
    Write-Host "GPU : no NVIDIA GPU detected (nvidia-smi not found)"
}

$code = @'
import json, aiclipper_env
aiclipper_env.apply()
from sharkyclips import hwprofile
s = hwprofile.summary()
try:
    import ctranslate2
    s["cuda_devices_for_whisper"] = ctranslate2.get_cuda_device_count()
    s["whisper_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda")) if s["cuda_devices_for_whisper"] else []
except Exception as e:  # noqa: BLE001
    s["cuda_devices_for_whisper"] = f"error: {e}"
print(json.dumps(s, indent=2, default=str))
'@
Push-Location (Join-Path $Root "app\backend")
try { $code | & $Py - } finally { Pop-Location }
