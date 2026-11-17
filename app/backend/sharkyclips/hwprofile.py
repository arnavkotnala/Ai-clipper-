"""ai-clipper: hardware profiles - one codebase, per-machine tuning picked automatically.

Profiles are JSON files in app/config/profiles/. The first profile whose "match" fits the
detected hardware wins (files are tried in "priority" order, highest first):

    {
      "name": "rtx5050-laptop", "priority": 50,
      "match": {"gpu_regex": "RTX 5050", "min_vram_gb": 7},
      "settings": {"vlm_frames": 6, ...},     # config defaults (Settings in the UI still win)
      "env": {"SHARKYCLIPS_GAME_WORKERS": "3"}  # process env defaults
    }

`local.json` (not in git) is applied last on top of the chosen profile, for one machine's quirks.
Nothing here is written to config.json, so copying the folder to another PC re-detects cleanly.
"""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path
from typing import Any

PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "profiles"
LOAD_ERRORS: dict[str, str] = {}  # file -> error; shown in Settings and by check-hardware


def _load_all() -> list[dict]:
    out = []
    if not PROFILES_DIR.is_dir():
        return out
    for f in sorted(PROFILES_DIR.glob("*.json")):
        if f.name == "local.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            data.setdefault("name", f.stem)
            data["_file"] = f.name
            out.append(data)
            LOAD_ERRORS.pop(f.name, None)
        except (OSError, json.JSONDecodeError) as exc:
            LOAD_ERRORS[f.name] = str(exc)  # a broken file must be visible, not silently skipped
            from .ui import console

            console.print(f"[red]Hardware profile {f.name} can't be read:[/] {exc}")
            continue
    return sorted(out, key=lambda p: -int(p.get("priority", 0)))


def _matches(match: dict, gpu_name: str | None, vram_gb: float, cpu: str, ram_gb: float) -> bool:
    if "gpu_regex" in match:
        if not gpu_name or not re.search(match["gpu_regex"], gpu_name, re.I):
            return False
    if match.get("requires_nvidia") and not gpu_name:
        return False
    if vram_gb < float(match.get("min_vram_gb", 0)):
        return False
    if "cpu_regex" in match and not re.search(match["cpu_regex"], cpu or "", re.I):
        return False
    if ram_gb < float(match.get("min_ram_gb", 0)):
        return False
    return True


@functools.lru_cache(maxsize=1)
def active() -> dict[str, Any]:
    """The profile for this machine (detected once per process)."""
    from . import hardware

    try:
        hw = hardware.detect_hardware()
        gpu = hw.best_nvidia
        gpu_name, vram = (gpu.name, gpu.vram_gb) if gpu else (None, 0.0)
        cpu, ram = hw.cpu_name, hw.ram_total_gb
    except Exception:  # noqa: BLE001 - detection must never break config loading
        gpu_name, vram, cpu, ram = None, 0.0, "", 0.0
    chosen: dict[str, Any] = {"name": "builtin-defaults", "settings": {}, "env": {}}
    for p in _load_all():
        if _matches(p.get("match") or {}, gpu_name, vram, cpu, ram):
            chosen = p
            break
    local = PROFILES_DIR / "local.json"
    if local.is_file():
        try:
            extra = json.loads(local.read_text(encoding="utf-8-sig"))
            chosen = {**chosen, "settings": {**chosen.get("settings", {}), **extra.get("settings", {})},
                      "env": {**chosen.get("env", {}), **extra.get("env", {})}, "local_override": True}
        except (OSError, json.JSONDecodeError):
            pass
    chosen["detected"] = {"gpu": gpu_name, "vram_gb": round(vram, 1), "cpu": cpu, "ram_gb": round(ram, 1)}
    return chosen


def settings() -> dict[str, Any]:
    if os.environ.get("AICLIPPER_TESTING") == "1" and not os.environ.get("AICLIPPER_PROFILE_TEST"):
        return {}  # tests see the plain built-in defaults
    return dict(active().get("settings") or {})


def apply_env() -> None:
    for k, v in (active().get("env") or {}).items():
        os.environ.setdefault(k, str(v))


def summary() -> dict[str, Any]:
    a = active()
    return {"name": a.get("name"), "file": a.get("_file"), "description": a.get("description"),
            "detected": a.get("detected"), "settings": a.get("settings"), "env": a.get("env"),
            "local_override": bool(a.get("local_override")), "load_errors": dict(LOAD_ERRORS)}
