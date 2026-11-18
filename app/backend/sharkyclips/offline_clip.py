"""ai-clipper: one-command OFFLINE clipper for a local video file, with measurements.

    python cli.py clip <video> [--top N] [--truth truth.txt] [--no-baseline] [--no-vision]

Stages: import -> transcribe -> highlights (baseline) -> highlights (ai-clipper) -> vision
check -> render 9:16 clips. While it runs it records, per stage: wall time, peak VRAM (nvidia-smi),
peak RAM of this process tree (incl. the private Ollama server), CPU use, and EVERY network
connection to a non-loopback address (the offline check). Output goes to
exports/<project-id>/: the clips, report.json and report.md.

Baseline = SharkyClips' own ranking (`rank`) with ai-clipper's motion signal switched off and no
vision check. ai-clipper = motion signal on, vision check, order by rank_score, candidates the
vision model rejects (likely_false_positive) dropped. Both are scored against the same truth file.

Truth file (one real highlight per line, written BEFORE looking at the results):
    01:12-01:25, multi_kill, triple kill with AWP
    1:03:40-1:03:52, funny, fell off the map
A detection is correct when the middle of a true event lies inside the clip; each true event can
be matched once. Timing error = |detected peak - middle of the true event|.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from .errors import SharkyClipsError

LOOPBACK = ("127.", "::1", "0.0.0.0", "::")


# ------------------------------------------------------------------ truth + evaluation


@dataclass
class TrueEvent:
    start: float
    end: float
    category: str = ""
    note: str = ""

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2


def parse_time(text: str) -> float:
    parts = [float(p) for p in text.strip().split(":")]
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_truth(text: str) -> list[TrueEvent]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([\d:.]+)\s*-\s*([\d:.]+)\s*(?:,\s*([^,]*))?(?:,\s*(.*))?$", line)
        if not m:
            raise SharkyClipsError(f"Truth file line {n} isn't 'mm:ss-mm:ss, category, note': {line}")
        start, end = parse_time(m.group(1)), parse_time(m.group(2))
        if end <= start:
            raise SharkyClipsError(f"Truth file line {n}: the end is before the start.")
        out.append(TrueEvent(start, end, (m.group(3) or "").strip(), (m.group(4) or "").strip()))
    return out


def evaluate(detections: list[dict], truth: list[TrueEvent]) -> dict:
    """detections: ranked dicts with start, end, peak. Greedy one-to-one matching."""
    used: set[int] = set()
    rows, errors, contained = [], [], 0
    for d in detections:
        inside = [i for i, ev in enumerate(truth) if i not in used and d["start"] <= ev.mid <= d["end"]]
        if inside:
            i = min(inside, key=lambda j: abs(truth[j].mid - d.get("peak", d["start"])))
            used.add(i)
            ev = truth[i]
            err = abs(d.get("peak", (d["start"] + d["end"]) / 2) - ev.mid)
            errors.append(err)
            whole = d["start"] <= ev.start and d["end"] >= ev.end
            contained += whole
            rows.append({**_brief(d), "match": i, "true_event": f"{_t(ev.start)}-{_t(ev.end)} {ev.category}".strip(),
                         "timing_error_s": round(err, 1), "whole_event_inside": whole})
        else:
            rows.append({**_brief(d), "match": None})
    tp = len(used)
    fp = len(detections) - tp
    fn = len(truth) - tp
    precision = tp / len(detections) if detections else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "detections": len(detections), "true_events": len(truth),
        "correct": tp, "false_positives": fp, "missed": fn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "mean_timing_error_s": round(sum(errors) / len(errors), 1) if errors else None,
        "max_timing_error_s": round(max(errors), 1) if errors else None,
        "whole_event_inside": contained,
        "missed_events": [f"{_t(truth[i].start)}-{_t(truth[i].end)} {truth[i].category}".strip()
                          for i in range(len(truth)) if i not in used],
        "rows": rows,
    }


def _t(sec: float) -> str:
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _brief(c: dict) -> dict:
    v = c.get("verification") or {}
    return {"id": c.get("id"), "start": round(float(c["start"]), 1), "end": round(float(c["end"]), 1),
            "peak": round(float(c.get("peak", c["start"])), 1), "category": c.get("category"),
            "confidence": c.get("confidence"), "engagement": c.get("engagement"),
            "vision": v.get("happened") or v.get("status")}


# ------------------------------------------------------------------ monitoring


def _tree(root: psutil.Process) -> list[psutil.Process]:
    procs = [root]
    try:
        procs += root.children(recursive=True)
    except psutil.Error:
        pass
    seen = {p.pid for p in procs}
    for p in psutil.process_iter(["name"]):  # the private Ollama server may predate this run
        name = (p.info.get("name") or "").lower()
        if name.startswith("ollama") and p.pid not in seen:
            procs.append(p)
            seen.add(p.pid)
    return procs


def _vram_mib() -> int | None:
    from .procs import no_window

    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, **no_window())
        return int(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


@dataclass
class StageStats:
    name: str
    seconds: float = 0.0
    peak_vram_mib: int | None = None
    peak_ram_mib: float = 0.0
    cpu_samples: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        cpu = self.cpu_samples
        return {"stage": self.name, "seconds": round(self.seconds, 1), "peak_vram_mib": self.peak_vram_mib,
                "peak_ram_mib": round(self.peak_ram_mib), "cpu_avg_pct": round(sum(cpu) / len(cpu)) if cpu else None,
                "cpu_peak_pct": round(max(cpu)) if cpu else None}


class Monitor:
    """Samples VRAM / RAM / CPU and network connections every `interval` seconds."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.root = psutil.Process()
        self.stages: list[StageStats] = []
        self.current: StageStats | None = None
        self.connections: dict[str, dict] = {}
        self.idle_vram_mib = _vram_mib()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._vram_every = 2  # nvidia-smi costs ~50 ms: every 2nd sample

    def start(self) -> None:
        psutil.cpu_percent(None)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    @contextmanager
    def stage(self, name: str):
        st = StageStats(name)
        self.stages.append(st)
        self.current = st
        t0 = time.perf_counter()
        try:
            yield st
        finally:
            st.seconds = time.perf_counter() - t0
            self.current = None

    def _loop(self) -> None:
        n = 0
        while not self._stop.wait(self.interval):
            n += 1
            st = self.current
            procs = _tree(self.root)
            rss = 0
            for p in procs:
                try:
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
                try:
                    for c in p.net_connections(kind="inet"):
                        if c.raddr and not str(c.raddr.ip).startswith(LOOPBACK):
                            key = f"{c.raddr.ip}:{c.raddr.port}"
                            if key not in self.connections:
                                self.connections[key] = {"remote": key, "process": p.name(), "pid": p.pid,
                                                         "stage": st.name if st else None, "status": c.status}
                except psutil.Error:
                    pass
            cpu = psutil.cpu_percent(None)
            if st is None:
                continue
            st.peak_ram_mib = max(st.peak_ram_mib, rss / 2**20)
            st.cpu_samples.append(cpu)
            if n % self._vram_every == 0:
                v = _vram_mib()
                if v is not None:
                    st.peak_vram_mib = max(st.peak_vram_mib or 0, v)


# ------------------------------------------------------------------ the run


@contextmanager
def _baseline_weights():
    """SharkyClips' own weights: ai-clipper's motion signal off (pipeline imported get_mode)."""
    from dataclasses import replace

    from .highlights import pipeline

    original = pipeline.get_mode

    def patched(name):
        preset = original(name)
        if "motion" in preset.weights:
            preset = replace(preset, weights={**preset.weights, "motion": 0.0})
        return preset

    pipeline.get_mode = patched
    try:
        yield
    finally:
        pipeline.get_mode = original


def _stop_private_ollama() -> int | None:
    """Stop the Ollama server listening on the private port (only called if this run started it)."""
    from urllib.parse import urlparse

    from . import llm

    port = urlparse(llm.base_url()).port
    for c in psutil.net_connections(kind="tcp"):
        if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
            try:
                proc = psutil.Process(c.pid)
                if proc.name().lower().startswith("ollama"):
                    for p in proc.children(recursive=True) + [proc]:
                        p.kill()
                    return c.pid
            except psutil.Error:
                return None
    return None


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:40] or "clip"


def run(video: str, top: int | None = None, truth_path: str | None = None, baseline: bool = True,
        vision: bool = True, subtitles: str = "bold", layout: str = "auto", mode: str = "gaming",
        log=print) -> dict:
    from . import verify as verify_mod
    from .clips import ClipOptions, Facecam, render_clip
    from .facecam import detect_for_project
    from .highlights import run_highlights
    from .ingest import ingest, is_url
    from .transcribe import transcribe_project

    if is_url(video):
        raise SharkyClipsError("The offline clipper takes a video FILE, not a link.",
                               "Download the video first (or import the link in the web UI).")
    src = Path(video).expanduser()
    if not src.is_file():
        raise SharkyClipsError(f"File not found: {src}")
    truth = parse_truth(Path(truth_path).read_text(encoding="utf-8-sig")) if truth_path else None

    # Offline: model libraries must use the local copies in app/models, never the hub.
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "SHARKYCLIPS_NO_DOWNLOADS"):
        os.environ[k] = "1"

    mon = Monitor()
    mon.start()
    report: dict[str, Any] = {"video": str(src.resolve()), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "idle_vram_mib": mon.idle_vram_mib}
    t_all = time.perf_counter()
    try:
        with mon.stage("import"):
            log("1/6 import (audio extraction)")
            project = ingest(str(src), mode=mode)
        meta = project.load_meta()
        report["project_id"] = project.id
        report["media"] = {k: meta.get(k) for k in ("duration", "resolution", "fps", "video_codec", "audio_codec")}
        # ai-clipper's exports folder (aiclipper_env sets SHARKYCLIPS_DOWNLOADS; tests: a temp dir)
        exports = Path(os.environ.get("SHARKYCLIPS_DOWNLOADS") or project.dir / "exports")
        out_dir = exports / project.id
        out_dir.mkdir(parents=True, exist_ok=True)

        with mon.stage("transcribe"):
            log("2/6 transcribe (Whisper, GPU if available)")
            tr = transcribe_project(project, force=True)
        report["transcript"] = {"language": tr.get("language"), "segments": len(tr.get("segments") or []),
                                "model": tr.get("model"), "device": tr.get("device"),
                                "compute_type": tr.get("compute_type")}

        if baseline:
            with mon.stage("highlights_baseline"):
                log("3/6 highlights - baseline (SharkyClips ranking, no motion signal, no vision)")
                with _baseline_weights():
                    base = run_highlights(project, top=top, force=True, fetch_chat=False)
            base_ranked = sorted(base["candidates"], key=lambda c: c["rank"])
            report["baseline"] = {"candidates": [_brief(c) for c in base_ranked]}
            shutil.copyfile(project.dir / "highlights.json", out_dir / "highlights_baseline.json")
        else:
            base_ranked = None

        with mon.stage("highlights"):
            log("4/6 highlights - ai-clipper (all signals incl. motion)")
            res = run_highlights(project, top=top, force=True, fetch_chat=False)

        vres = None
        if vision:
            from . import config, llm

            started_ollama = llm.server_version(0.5) is None  # we start it -> we stop it afterwards
            with mon.stage("vision_check"):
                log("5/6 vision check (local qwen3-vl via the private Ollama server)")
                try:
                    vres = verify_mod.verify_project(
                        project, force=True, on_progress=lambda i, n: log(f"     candidate {min(i + 1, n)}/{n}"))
                    res = vres["result"]
                except SharkyClipsError as exc:
                    log(f"     vision check skipped: {exc}")
                    report["vision_error"] = str(exc)
                finally:
                    if started_ollama:
                        _stop_private_ollama()
            report["vision_model"] = config.get("vlm_model")
        by_id = {c["id"]: c for c in res["candidates"]}
        ordered = [by_id[i] for i in res.get("order") or [] if i in by_id] or sorted(res["candidates"], key=lambda c: c["rank"])
        final = [c for c in ordered if not c.get("likely_false_positive")]
        dropped = [c for c in ordered if c.get("likely_false_positive")]
        report["ai_clipper"] = {"candidates": [_brief(c) for c in ordered],
                                "dropped_by_vision": [_brief(c) for c in dropped]}
        if vres:
            report["vision"] = {"checked": vres.get("checked"), "errors": vres.get("errors"), "seconds": vres.get("seconds")}

        with mon.stage("facecam"):
            log("     facecam detection (for the split facecam / gameplay layout)")
            fres = detect_for_project(project, use_memory=False)
        conf = float(fres.get("confidence") or 0)
        facecam = Facecam.from_dict(fres["facecam"]) if fres.get("facecam") and conf >= 0.5 else None
        report["facecam"] = {"found": bool(fres.get("facecam")), "confidence": round(conf, 2),
                             "used": facecam is not None, "box": fres.get("facecam")}

        with mon.stage("render"):
            log(f"6/6 render {len(final)} clips (9:16, subtitles={subtitles})")
            clips = []
            opts = ClipOptions.from_dict({"layout": layout, "subtitles": subtitles})
            for n, c in enumerate(final, 1):
                name = f"{n:02d}_{_safe(c.get('category') or 'clip')}_{_t(c['start']).replace(':', 'm')}s.mp4"
                r = render_clip(project, c, opts, facecam, out_dir / name)
                clips.append({"file": name, **_brief(c), "duration_s": round(float(c["end"]) - float(c["start"]), 1),
                              "render_s": round(r.seconds, 1), "encoder": r.encoder,
                              "size_mb": round(r.size_bytes / 2**20, 1)})
                log(f"     {name}  {clips[-1]['duration_s']} s  rendered in {clips[-1]['render_s']} s ({r.encoder})")
        report["clips"] = clips
        report["clip_lengths_ok_20_60s"] = all(20 <= c["duration_s"] <= 60 for c in clips)

        if truth is not None:
            report["truth_file"] = str(Path(truth_path).resolve())
            report["evaluation"] = {"ai_clipper": evaluate(final, truth)}
            if base_ranked is not None:
                report["evaluation"]["baseline"] = evaluate(base_ranked, truth)
    finally:
        mon.stop()
        report["seconds_total"] = round(time.perf_counter() - t_all, 1)
        report["stages"] = [s.to_dict() for s in mon.stages]
        report["network_connections"] = list(mon.connections.values())
        report["offline_ok"] = not mon.connections

    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    report["out_dir"] = str(out_dir)
    return report


def to_markdown(r: dict) -> str:
    L = [f"# Offline clipper report: {Path(r['video']).name}", "",
         f"- Started {r['started']}, total {r['seconds_total']} s; video {_t(r['media']['duration'] or 0)}, "
         f"{r['media']['resolution']} @ {r['media']['fps']} fps",
         f"- Offline check: {'no connection to any non-local address' if r['offline_ok'] else 'CONNECTIONS SEEN: ' + json.dumps(r['network_connections'])}",
         f"- Transcript: {r.get('transcript')}",
         f"- Facecam: {r.get('facecam')}",
         f"- Vision check: {r.get('vision')}, model {r.get('vision_model')}"
         + (f", ERROR: {r['vision_error']}" if r.get("vision_error") else ""),
         f"- All clips 20-60 s: {r.get('clip_lengths_ok_20_60s')}", ""]
    L += ["| Stage | Time (s) | Peak VRAM (MiB) | Peak RAM (MiB) | CPU avg / peak (%) |", "|---|---|---|---|---|"]
    for s in r["stages"]:
        L.append(f"| {s['stage']} | {s['seconds']} | {s['peak_vram_mib']} | {s['peak_ram_mib']} | {s['cpu_avg_pct']} / {s['cpu_peak_pct']} |")
    L += ["", f"Idle VRAM before the run: {r.get('idle_vram_mib')} MiB (other programs).", "", "## Clips", ""]
    L += ["| File | Time | Length (s) | Category | Conf | Eng | Vision | Render (s) |", "|---|---|---|---|---|---|---|---|"]
    for c in r.get("clips") or []:
        L.append(f"| {c['file']} | {_t(c['start'])}-{_t(c['end'])} | {c['duration_s']} | {c['category']} | "
                 f"{c['confidence']} | {c['engagement']} | {c['vision']} | {c['render_s']} ({c['encoder']}) |")
    for d in (r.get("ai_clipper") or {}).get("dropped_by_vision") or []:
        L.append(f"| (dropped by vision check) | {_t(d['start'])}-{_t(d['end'])} | | {d['category']} | {d['confidence']} | {d['engagement']} | {d['vision']} | |")
    ev = r.get("evaluation")
    if ev:
        L += ["", "## Accuracy against the truth file", "",
              "| | Detections | Correct | False pos. | Missed | Precision | Recall | F1 | Mean timing error (s) | Whole event inside |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for name in ("baseline", "ai_clipper"):
            e = ev.get(name)
            if e:
                L.append(f"| {name} | {e['detections']} | {e['correct']} | {e['false_positives']} | {e['missed']} | "
                         f"{e['precision']} | {e['recall']} | {e['f1']} | {e['mean_timing_error_s']} | {e['whole_event_inside']} |")
        for name in ("baseline", "ai_clipper"):
            e = ev.get(name)
            if e and e["missed_events"]:
                L.append(f"\n{name} missed: {', '.join(e['missed_events'])}")
    return "\n".join(L) + "\n"
