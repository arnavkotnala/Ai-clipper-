"""Benchmark local vision models for ai-clipper's Stage C (verification).

    .venv\\Scripts\\python scripts\\bench_vlm.py qwen2.5vl:3b qwen3-vl:4b [--frames 6] [--repeats 2]

Measures, per model: cold load time, seconds per candidate (warm), output tokens/s, peak VRAM
(nvidia-smi polled every 0.2 s) and answers on three SANITY PROBES with a known answer:

    static   - the same calm frame 6 times             expected: happened = no / unclear
    menu     - a settings menu with no gameplay         expected: happened = no / unclear
    banner   - a big "TRIPLE KILL" banner + kill feed   expected: happened = yes, event multi_kill

These probes only show whether a model hallucinates events on empty input and can read an
obvious banner. They are NOT an accuracy measurement: that needs labelled real gameplay
(scripts/evaluate.py). Results are printed and appended to logs/bench_vlm.jsonl.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "backend"))
import aiclipper_env  # noqa: E402

aiclipper_env.apply()
from sharkyclips import llm, verify  # noqa: E402


def _font(size: int):
    from PIL import ImageFont

    for name in ("arialbd.ttf", "arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _jpeg(img) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=85)
    return b.getvalue()


def probe_frames(kind: str, k: int) -> list[bytes]:
    from PIL import Image, ImageDraw

    out = []
    for i in range(k):
        img = Image.new("RGB", (672, 378), (70, 90, 70))
        d = ImageDraw.Draw(img)
        if kind == "static":
            d.rectangle([0, 250, 672, 378], fill=(110, 95, 70))  # ground
            d.rectangle([300, 150, 380, 250], fill=(90, 90, 100))  # a crate
        elif kind == "menu":
            img.paste((25, 25, 35), [0, 0, 672, 378])
            d.text((40, 30), "SETTINGS", fill=(230, 230, 230), font=_font(40))
            for j, item in enumerate(["Graphics", "Audio", "Controls", "Keybinds", "Back"]):
                d.text((60, 100 + 50 * j), item, fill=(200, 200, 200), font=_font(26))
        elif kind == "banner":
            d.rectangle([0, 250, 672, 378], fill=(110, 95, 70))
            d.ellipse([280 + 10 * i, 120, 340 + 10 * i, 250], fill=(160, 40, 40))  # an enemy
            feed = ["You  >  Raven", "You  >  Ghost", "You  >  Viper"][: min(3, 1 + i)]
            for j, row in enumerate(feed):
                d.rectangle([440, 10 + 30 * j, 665, 36 + 30 * j], fill=(20, 20, 20))
                d.text((450, 12 + 30 * j), row, fill=(255, 255, 255), font=_font(18))
            if i >= k // 2:
                d.text((150, 60), "TRIPLE KILL", fill=(255, 215, 0), font=_font(56))
        out.append(_jpeg(img))
    return out


EXPECT = {"static": ("no", "unclear"), "menu": ("no", "unclear"), "banner": ("yes",)}


class VramPeak:
    def __init__(self):
        self.peak, self._stop = 0, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                   capture_output=True, text=True, timeout=5)
                self.peak = max(self.peak, int(r.stdout.strip().splitlines()[0]))
            except Exception:
                pass
            time.sleep(0.2)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()


def vram_now() -> int:
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=5)
    return int(r.stdout.strip().splitlines()[0])


def bench(model: str, k: int, repeats: int) -> dict:
    for m in llm.loaded_models():  # idle must mean NOTHING loaded (an earlier run skewed this)
        llm.unload(m)
    for _ in range(30):
        if not llm.loaded_models():
            break
        time.sleep(0.5)
    time.sleep(1)
    idle = vram_now()
    times = verify.frame_times(0, 30, 15, k)
    rows, calls = [], []
    with VramPeak() as vp:
        t0 = time.perf_counter()
        # cold call = load + first answer
        verify.ask(model, probe_frames("static", k), verify.build_prompt(times, 15, "", None), k)
        cold = time.perf_counter() - t0
        for _ in range(repeats):
            for kind in ("static", "menu", "banner"):
                t1 = time.perf_counter()
                try:
                    ans = verify.ask(model, probe_frames(kind, k), verify.build_prompt(times, 15, "", None), k)
                    ok = ans["happened"] in EXPECT[kind] and (kind != "banner" or ans["event"] == "multi_kill")
                    rows.append({"probe": kind, "pass": ok, "happened": ans["happened"], "event": ans["event"],
                                 "confidence": ans["confidence"], "evidence": ans["evidence"][:160]})
                    calls.append((time.perf_counter() - t1, ans.get("_eval_count") or 0))
                except Exception as exc:  # noqa: BLE001
                    rows.append({"probe": kind, "pass": False, "error": str(exc)[:200]})
    llm.unload(model)
    secs = [c[0] for c in calls]
    return {"model": model, "frames": k, "cold_load_and_first_s": round(cold, 1),
            "per_candidate_s_mean": round(sum(secs) / len(secs), 2) if secs else None,
            "per_candidate_s_max": round(max(secs), 2) if secs else None,
            "vram_idle_mib": idle, "vram_peak_mib": vp.peak, "vram_model_mib": vp.peak - idle,
            "probes_passed": sum(r["pass"] for r in rows), "probes_total": len(rows), "probes": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args()
    llm.ensure_server()
    out = ROOT / "logs" / "bench_vlm.jsonl"
    for m in a.models:
        res = bench(m, a.frames, a.repeats)
        res["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")
        print(json.dumps({k: v for k, v in res.items() if k != "probes"}, indent=1))
        for r in res["probes"]:
            print("  ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
