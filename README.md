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

## Setup status (2026-11-17)

Done: folder skeleton created, both repositories cloned.

Machine checked at setup:

| Item | Found | Note |
|---|---|---|
| Python | 3.14.7 only | SharkyClips supports **3.11–3.13**. ML wheels (faster-whisper/CTranslate2, onnxruntime, torch) often lag new Python versions. Plan: a project-local Python 3.12 venv. |
| Node.js | 24.19.0 | OK for the SharkyClips frontend and for yt-dlp's YouTube JS runtime. |
| ffmpeg / ffprobe | **not on PATH** | Required by both projects. |
| GPU | NVIDIA RTX 5050 Laptop (8 GB, Blackwell), driver 621.14 | Blackwell needs CUDA 12.8+ builds; older CUDA wheels will fail or fall back to CPU. |
| RAM / disk | 23 GB / 413 GB free | Enough. |

## Licensing

SharkyClips is MIT: code may be reused if its copyright notice is kept.
Clippie has **no license file**, which by default means all rights are reserved. Treat it as a
design reference, and ask its author before copying its code into `app/`.
