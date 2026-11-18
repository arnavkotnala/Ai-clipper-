# Troubleshooting

Each entry is a problem that actually happened while building or testing ai-clipper, or a
check the scripts make. Run `scripts\check-hardware.ps1` first: it shows what the app detects.

## Setup

**`setup.ps1` stops at "uv could not install Python" with "operation timed out".**
The Python download (~21 MB) is slow on some connections. The script already waits up to 5 min per
request and retries 5 times; run it again. Copying the `tools\python` folder from a PC that already
has it avoids the download.

**`setup.ps1` says ".venv was made on another PC or folder: rebuilding it".**
Expected after copying the folder: a Python venv stores absolute paths. The rebuild uses the
copied package cache (`tools\_uvcache`) and works offline if that folder was copied too.

**"Some packages are not in tools\_uvcache" with `-Offline`.**
The cache did not come along. Run `setup.ps1` once with internet.

**"The pinned FFmpeg build is gone".**
FFmpeg-Builds deletes old releases after a few weeks. The script then installs today's n8.1 build,
verified against GitHub's published sha256. Note the new version in `docs\RESULTS.md`.

**Running scripts is blocked ("running scripts is disabled on this system").**
Start them as shown in the README: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`.
This changes nothing system-wide.

## Starting the app

**The page does not open / http://127.0.0.1:8100 does not answer.**
The first start takes a while (here up to ~90 s, see the server log). Check `logs\server-dev.log`
if you started it from a terminal, or run `scripts\run.ps1` and read its output. If another
program uses port 8100: `scripts\run.ps1 -Port 8200`.

**"localhost" is slow but 127.0.0.1 is fast.**
Windows tries IPv6 (`::1`) first; the server listens on IPv4. Use http://127.0.0.1:8100.

**The interface is in Turkish.**
SharkyClips' default was Turkish; ai-clipper's default is English. An older `data\app\config.json`
can still say `"ui_language": "tr"`: pick English in the language menu (top right), or set
`ui_language` and `output_language` to `"en"` in that file.

**A browser tab opens every time the server starts.**
That is `cli.py serve`'s default. Use `scripts\run.ps1 -NoBrowser`.

## Import

**A link shows "This stream is live right now."**
Live streams cannot be clipped; wait until it ends and use the VOD link.

**"This video needs a signed-in account (age-restricted, private or members-only)".**
ai-clipper never logs in to sites and does not bypass access restrictions. Download the video
yourself (if you are allowed to) and import the file.

**"Find clips" stays disabled after pasting a link.**
Either the link is still being read (a spinner shows), it failed (the error shows under the link),
or the video is already a project ("Open it" link).

## GPU and AI

**Rendering is slow / "NVENC test encode failed".**
NVENC needs an NVIDIA driver that supports the GPU (RTX 50 series = Blackwell: recent driver). The
app then falls back to the CPU encoder (libx264), which works but is slower.

**Whisper runs on the CPU ("CUDA devices visible to Whisper: 0").**
Check `nvidia-smi` works. The CUDA libraries come from pip (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12` in `requirements.lock.txt`); no CUDA Toolkit install is needed.

**The vision check never runs.**
It needs Ollama installed (`%LOCALAPPDATA%\Programs\Ollama\ollama.exe`) and the model in
`app\models\ollama` (`setup.ps1 -Models`). The app starts its own Ollama on port 11435; your normal
Ollama (port 11434) is not used.

## Publishing

**There is no "Publish" / YouTube button.**
On purpose: ai-clipper is offline-first and never uploads or posts. Finished clips are MP4 files:
use the download button, or find them in the project's `clips` folder under `data\projects`.

## Tests

**`test_web.py::test_projects_empty` and `test_pubqueue_status.py::test_browser_posts_land_in_the_library_and_the_digest` fail.**
Known: they also fail on the untouched SharkyClips code when the whole suite runs, and pass when
run alone (they depend on test order).
