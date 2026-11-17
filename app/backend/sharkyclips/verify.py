"""ai-clipper Stage C: check highlight candidates with a local vision-language model (Ollama).

Candidate-first funnel: the whole video is NEVER sent to the model. For each candidate we send
4-8 frames sampled symmetrically around its peak (hardware profile `vlm_frames`) plus the
transcript around the peak, and ask a structured question. The model only ever sees those
frames, so it can tighten boundaries between them but cannot invent new ones.

Design decisions (and why):
  * The detector's own guess (category, reasons) is NOT put in the prompt: it would prime the
    model to confirm it. The model describes what it sees, and we compare.
  * "unclear" is an explicit, acceptable answer. A small VLM that can't see the kill feed must
    say so instead of guessing. unclear leaves confidence unchanged.
  * JSON schema via Ollama structured outputs + validation; one retry; on failure the candidate
    gets status "error" and keeps its detector scores (never a silent pass).
  * GPU: Whisper is released before the VLM loads (memory.release_current); the VLM is unloaded
    after the batch (keep_alive 0) and whenever the governor pauses (Gaming mode).

Re-implemented for ai-clipper; the idea (VLM verification of detector candidates) is from
Clippie's design notes, the code is new. See docs/PROVENANCE.md.
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from . import config, governor, llm, memory, procs, scoring
from .errors import SharkyClipsError
from .project import Project, write_json_atomic

PROMPT_VERSION = 1
FRAME_WIDTH = 672  # multiple of 28 (Qwen-VL patch grid); enough to read big on-screen text
SPAN_S = 12.0  # frames are taken from at most +-6 s around the peak
EVENTS = [c for c in scoring.CATEGORIES] + ["none"]

SCHEMA = {
    "type": "object",
    "properties": {
        "happened": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "event": {"type": "string", "enum": EVENTS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "interest": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "string"},
        "evidence_frames": {"type": "array", "items": {"type": "integer"}},
        "action_starts_frame": {"type": "integer"},
        "action_ends_frame": {"type": "integer"},
    },
    "required": ["happened", "event", "confidence", "interest", "evidence", "evidence_frames",
                 "action_starts_frame", "action_ends_frame"],
}

SYSTEM = (
    "You review short moments from gameplay recordings for a highlight editor. "
    "You get a few frames in time order (numbered, with timestamps) and what the player said. "
    "Judge ONLY what is visible in the frames or stated in the transcript. "
    "If the frames don't show clear evidence of an event, answer happened=\"unclear\" or \"no\"; "
    "never guess game events (kills, wins) you cannot see. "
    "On-screen HUD changes ARE visible evidence: a kill/elimination/score counter that goes up "
    "between frames, new kill-feed lines, a health bar dropping to almost nothing, a round or "
    "victory banner. Compare the frames with each other and report such changes. "
    "evidence: one or two sentences naming the frame numbers and what is visible in them. "
    "confidence: how sure you are about happened/event (0-1). "
    "interest: how entertaining this moment is for a short video viewer (0-1); calm menus, "
    "loading screens and walking around are low. "
    "action_starts_frame / action_ends_frame: first and last frame number showing the action, "
    "or -1 for both when no action is visible.\n"
    "event (pick the most specific that the evidence supports): "
    "multi_kill = two or more kills in quick succession (e.g. DOUBLE/TRIPLE/QUADRA KILL, ACE, several "
    "kill-feed entries by the player); kill = one kill by the player; clutch = winning while "
    "outnumbered or last alive; unexpected_kill = a kill that comes out of nowhere or in an odd way; "
    "high_skill = a visibly difficult play (flick, trick shot, parkour); death = the player dies; "
    "unexpected_death = a sudden or silly death; near_death = surviving with almost no health; "
    "intense_fight = sustained combat; close_victory = winning a round/match by a small margin; "
    "celebration = victory screen or cheering; funny = something comedic; reaction = the player's "
    "strong reaction is the main content; commentary = talking is the main content; "
    "rare_event = something unusual for the game; unexpected_event = a surprising non-combat moment; "
    "other = gameplay without a clear event; none = no gameplay (menu, loading, black screen)."
)


def default_model() -> str:
    return str(config.get("vlm_model") or "qwen3-vl:4b")


def frame_times(start: float, end: float, peak: float, k: int) -> list[float]:
    """k timestamps symmetric around the peak, inside [start, end] (fixes the classic bug of
    sampling the whole span and then truncating to the first k frames)."""
    k = max(2, int(k))
    half = SPAN_S / 2
    a, b = max(start, peak - half), min(end, peak + half)
    if b - a < 1.0:
        a, b = start, end
    if b <= a:
        return [round(a, 2)] * k
    step = (b - a) / (k - 1)
    return [round(a + i * step, 2) for i in range(k)]


def extract_frame(src: Path, t: float, width: int = FRAME_WIDTH) -> bytes:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}",
           "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe",
           "-vcodec", "mjpeg", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=60, **procs.no_window())
    if r.returncode != 0 or not r.stdout:
        raise SharkyClipsError(f"Couldn't read a frame at {t:.1f}s: {r.stderr.decode(errors='replace')[-200:]}")
    return r.stdout


def transcript_context(project: Project, peak: float, before: float = 10.0, after: float = 8.0) -> str:
    if not project.transcript_path.is_file():
        return ""
    words = []
    for seg in project.load_transcript().get("segments", []):
        for w in seg.get("words", []):
            if peak - before <= float(w["start"]) <= peak + after:
                words.append(w["word"].strip())
    return " ".join(words)[:600]


def build_prompt(times: list[float], peak: float, said: str, game: str | None) -> str:
    lines = [f"Game: {game}." if game else "Game: unknown (judge from the frames).",
             "Frames: " + ", ".join(f"#{i + 1} at {t - peak:+.1f}s" for i, t in enumerate(times)) +
             " (0s = the moment the detector flagged).",
             f"Player said around then: \"{said}\"" if said else "Player said nothing (or no transcript).",
             "What happens in these frames? Is it a highlight-worthy gameplay event?"]
    return "\n".join(lines)


def validate(data: dict, k: int) -> list[str]:
    errs = llm.validate(data, SCHEMA)
    for key in ("action_starts_frame", "action_ends_frame"):
        if isinstance(data.get(key), int) and data[key] != -1 and not 1 <= data[key] <= k:
            errs.append(f"{key} must be -1 or between 1 and {k}")
    return errs


def ask(model: str, images: list[bytes], prompt: str, k: int, http=None, timeout: float = 300) -> dict:
    """One structured VLM call (+1 retry on invalid output). Returns the parsed answer."""
    http = http or llm._http()
    # Context must hold every image: measured ~1080 tokens per 672 px frame with Qwen2.5-VL
    # (6 frames = 6471 prompt tokens), so Ollama's default 4096 overflows. Size it per request.
    num_ctx = min(16384, 1200 * len(images) + 1536)
    body = {"model": model, "stream": False, "think": False, "format": SCHEMA, "keep_alive": "3m",
            "options": {"temperature": 0, "num_predict": 400, "num_ctx": num_ctx},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt,
                          "images": [base64.b64encode(b).decode() for b in images]}]}
    last = ""
    for _attempt in range(2):
        r = http.post(f"{llm.base_url()}/api/chat", json=body, timeout=timeout)
        if not r.ok:
            raise SharkyClipsError(f"Vision model error: {r.text[:300]}")
        resp = r.json()
        msg = resp.get("message", {})
        content = msg.get("content", "")
        if not content.strip() and (msg.get("thinking") or "").strip().startswith("{"):
            # Ollama 0.33 quirk (seen with qwen3-vl + think=false + a format schema): the
            # schema-constrained answer is returned in "thinking" and "content" is empty.
            content = msg["thinking"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            last = f"not JSON: {content[:120]}"
            continue
        errs = validate(data, k)
        if not errs:
            data["_eval_count"] = resp.get("eval_count")
            return data
        last = "; ".join(errs)
    raise SharkyClipsError(f"Vision model gave an invalid answer ({last}).")


def to_verification(ans: dict, times: list[float], model: str, seconds: float) -> dict:
    k = len(times)
    a, b = int(ans["action_starts_frame"]), int(ans["action_ends_frame"])
    if a == -1 or b == -1:  # the model saw no action: no boundary suggestion
        sug_start = sug_end = None
    else:
        s_idx, e_idx = sorted((min(max(1, a), k) - 1, min(max(1, b), k) - 1))
        sug_start, sug_end = times[s_idx], times[e_idx]
    frames = sorted({int(f) for f in ans.get("evidence_frames") or [] if 1 <= int(f) <= k})
    return {
        "status": "ok", "happened": ans["happened"], "event": ans["event"],
        "confidence": round(float(ans["confidence"]), 3), "interest": round(float(ans["interest"]), 3),
        "evidence": str(ans["evidence"])[:400], "evidence_frames": frames,
        "evidence_times": [times[f - 1] for f in frames],
        "suggested_start": sug_start, "suggested_end": sug_end,
        "frame_times": times, "model": model, "seconds": round(seconds, 2),
        "prompt_version": PROMPT_VERSION,
    }


def verify_project(project: Project, model: str | None = None, frames: int | None = None,
                   limit: int | None = None, force: bool = False,
                   on_progress: Callable[[int, int], None] | None = None,
                   http=None) -> dict:
    """Run the vision check on a project's candidates (best rank_score first) and re-score."""
    from .highlights.modes import get_mode

    path = project.dir / "highlights.json"
    if not path.is_file():
        raise SharkyClipsError("Find highlights first.")
    result = json.loads(path.read_text(encoding="utf-8"))
    src = project.source_path
    if src is None or not src.is_file():
        raise SharkyClipsError("The source video is missing, so frames can't be checked.")
    model = model or default_model()
    k = int(frames or config.get("vlm_frames") or 6)
    k = min(8, max(4, k))
    cands = sorted(result.get("candidates", []), key=lambda c: -c.get("rank_score", 0))
    todo = [c for c in cands if force or (c.get("verification") or {}).get("status") != "ok"
            or (c.get("verification") or {}).get("model") != model]
    if limit:
        todo = todo[:limit]
    meta = project.load_meta()
    game = meta.get("game_name") or meta.get("game")

    stats = {"model": model, "frames": k, "checked": 0, "errors": 0, "seconds": 0.0}
    if not todo:
        return {**stats, "result": result}
    llm.ensure_server()
    memory.release_current()  # Whisper (or anything else heavy) leaves the GPU first
    t_all = time.perf_counter()
    try:
        for i, c in enumerate(todo):
            governor.checkpoint(release=lambda: llm.unload(model))
            if on_progress:
                on_progress(i, len(todo))
            times = frame_times(float(c["start"]), float(c["end"]), float(c.get("peak", c["start"])), k)
            t0 = time.perf_counter()
            try:
                images = [extract_frame(src, t) for t in times]
                prompt = build_prompt(times, float(c.get("peak", c["start"])),
                                      transcript_context(project, float(c.get("peak", c["start"]))), game)
                ans = ask(model, images, prompt, k, http=http)
                c["verification"] = to_verification(ans, times, model, time.perf_counter() - t0)
                if ans["happened"] == "yes" and ans["event"] not in ("none",):
                    c["category_vision"] = ans["event"]
                stats["checked"] += 1
            except SharkyClipsError as exc:
                c["verification"] = {"status": "error", "error": exc.message[:300], "model": model,
                                     "seconds": round(time.perf_counter() - t0, 2)}
                stats["errors"] += 1
            # Save after every candidate: a pause / crash keeps the finished ones.
            preset = get_mode(result.get("mode") or "gaming")
            scoring.apply(result, post_roll=preset.post_roll, clip_max=preset.clip_max)
            write_json_atomic(path, result)
    finally:
        llm.unload(model)
    stats["seconds"] = round(time.perf_counter() - t_all, 2)
    result["verification"] = {k2: v for k2, v in stats.items() if k2 != "result"}
    write_json_atomic(path, result)
    if on_progress:
        on_progress(len(todo), len(todo))
    return {**stats, "result": result}
