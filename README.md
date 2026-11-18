# ai-clipper

Single root folder for the unified AI clipping application. Everything for this project lives here.

## Layout

```
ai-clipper/
├── repo/                 Upstream source, read-only reference. Do not edit.
│   ├── sharkyclips/      https://github.com/utkualsir/sharkyclips  @ cf7a5a0 (MIT)
│   └── clippie/          https://github.com/DRIRU/clippie         @ 7044175 (no license file)
├── app/                  The unified application (all new code goes here)
│   ├── frontend/
│   ├── backend/
│   ├── models/           Downloaded ML models (Whisper, OCR, etc.)
│   ├── database/         SQLite database files
│   └── tests/
├── data/                 Working data (analysis caches, intermediate files)
├── downloads/            Downloaded source videos (VODs, YouTube)
├── exports/              Rendered clips
├── logs/
├── scripts/              Setup / run / maintenance scripts
└── docs/
```

## Rules

- `repo/` holds the original repositories unchanged. Check with
  `git -C repo/sharkyclips status` and `git -C repo/clippie status`; both should report a clean tree.
  To update them, use `git pull` inside each one. Never edit them by hand.
- All paths in the app are relative to this folder, so the folder can be copied to another
  Windows 11 PC. Virtual environments, `node_modules` and GPU-specific packages are
  rebuilt there by the setup script; they are not meant to be copied.

## Install and run (Windows 11)

```powershell
# once per PC (after copying the ai-clipper folder or cloning the repo)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1            # add -Models to fetch the AI models
# every time
powershell -ExecutionPolicy Bypass -File scripts\run.ps1              # http://127.0.0.1:8100
# what was detected on this PC (use it on PC B)
powershell -ExecutionPolicy Bypass -File scripts\check-hardware.ps1
```

`setup.ps1` writes only inside this folder and needs no admin rights: uv and Python 3.12
(`tools\`), the Python environment (`.venv`, pinned by `app\backend\requirements.lock.txt`),
FFmpeg with NVENC (`tools\ffmpeg`, sha256-checked; versions in `scripts\toolchain.lock.json`).
The vision check needs [Ollama](https://ollama.com) installed; the app runs its own private
Ollama server on port 11435 with the models in `app\models\ollama`.

**Moving to PC B:** copy the whole `ai-clipper` folder (including `tools\` and `app\models\`),
then run `setup.ps1` there. It rebuilds `.venv` (a venv stores absolute paths) from the copied
package cache, and `check-hardware.ps1` shows which hardware profile was picked.

Problems: see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Status (2026-11-18)

| Part | State |
|---|---|
| Foundation | SharkyClips v1.2.50 (MIT), see [docs/PROVENANCE.md](docs/PROVENANCE.md) |
| Import | Local files; links (YouTube, Twitch, Kick, ...) with preview, quality choice, cancel, duplicate check |
| Highlight funnel | Stage A signals incl. new motion/scene signal; Stage B candidates; Stage C vision check (qwen3-vl:4b); Stage D phases + two scores |
| Offline-first | Uploads, posting and online updates are blocked in code (`outbound.py`) and hidden in the UI |
| Hardware profiles | PC A (RTX 5050) verified; PC B (RTX 5060) profile written, **not yet verified on that PC** |
| UI | SharkyClips' UI, English by default. The new scores and vision verdicts are **not shown yet** |
| Real-recording accuracy | **Not measured yet** (needs a real recording + your own list of highlights) |

Measured numbers are in [docs/RESULTS.md](docs/RESULTS.md); nothing is claimed there that was not measured.

## Licensing

SharkyClips is MIT: code may be reused if its copyright notice is kept.
Clippie has **no license file**, which by default means all rights are reserved. Treat it as a
design reference, and ask its author before copying its code into `app/`.
