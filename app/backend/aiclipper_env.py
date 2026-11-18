"""ai-clipper: keep every file the app writes inside the ai-clipper folder.

Imported first by cli.py (every entry point: serve, launch, doctor, the pipeline commands).
Paths are derived from this file's location, never stored as absolute paths, so the whole
ai-clipper folder can be copied to another Windows PC and still work.

Tests do NOT import this module: conftest.py gives them a throwaway home instead.

Layout (ROOT = ai-clipper/):
    data/app         settings (config.json), logs, small caches   SHARKYCLIPS_HOME
    data/projects    one folder per project: source video, analysis, clips
    exports          finished clips the user saves                 SHARKYCLIPS_DOWNLOADS
    downloads        YouTube downloads before import
    app/models       all downloaded models (Whisper, Ollama, ONNX)
    tools/ffmpeg     portable FFmpeg (put first on PATH)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def paths() -> dict[str, Path]:
    return {
        "root": ROOT,
        "home": ROOT / "data" / "app",
        "projects": ROOT / "data" / "projects",
        "exports": ROOT / "exports",
        "downloads": ROOT / "downloads",
        "logs": ROOT / "logs",
        "models": ROOT / "app" / "models",
        "ffmpeg_bin": ROOT / "tools" / "ffmpeg" / "bin",
    }


def apply() -> dict[str, Path]:
    p = paths()
    # Tests import cli.py too: they must keep their throwaway home and never see the real
    # data/projects or models (conftest.py sets AICLIPPER_TESTING before anything is imported).
    if os.environ.get("AICLIPPER_TESTING") == "1" or "pytest" in sys.modules:
        return p
    env = {
        "SHARKYCLIPS_HOME": p["home"],
        "SHARKYCLIPS_WORKSPACE": p["projects"],
        "SHARKYCLIPS_DOWNLOADS": p["exports"],
        "SHARKYCLIPS_QUEUE_HOME": p["home"] / "queue",
        "SHARKYCLIPS_MODELS": p["models"],
        "HF_HOME": p["models"] / "huggingface",
        "OLLAMA_MODELS": p["models"] / "ollama",
        # Private Ollama server: own port, so the user's normal Ollama (11434) is never touched.
        "AICLIPPER_OLLAMA_URL": "http://127.0.0.1:11435",
        # Measured 2026-11-18: without this, `ollama serve` (0.33.2) opens an HTTPS connection to
        # a Google Cloud address (34.36.133.15:443) at startup; with it, none.
        "OLLAMA_NO_CLOUD": "1",
        "AICLIPPER_OLLAMA_EXE": Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    }
    for key, value in env.items():
        os.environ.setdefault(key, str(value))
    # Offline-first: no migration of other installs' shortcuts, no publishing or live-chat loops,
    # no Hugging Face telemetry.
    os.environ.setdefault("SHARKYCLIPS_NO_MIGRATE", "1")
    os.environ.setdefault("SHARKYCLIPS_PUBLISH_LOOP", "0")
    os.environ.setdefault("SHARKYCLIPS_LIVE_LOOP", "0")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("DO_NOT_TRACK", "1")
    if p["ffmpeg_bin"].is_dir():
        path = os.environ.get("PATH", "")
        if str(p["ffmpeg_bin"]) not in path.split(os.pathsep):
            os.environ["PATH"] = str(p["ffmpeg_bin"]) + os.pathsep + path
    for key in ("home", "projects", "exports", "downloads", "logs", "models"):
        p[key].mkdir(parents=True, exist_ok=True)
    # Per-machine tuning (app/config/profiles): process env defaults like decoder workers.
    from sharkyclips import hwprofile

    hwprofile.apply_env()
    return p
