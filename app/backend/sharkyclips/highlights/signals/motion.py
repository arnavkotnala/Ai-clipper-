"""Motion + scene change: general visual activity, for any game (ai-clipper).

Why: the audio signals miss quiet-but-hectic gameplay (a silent player in a firefight), and
game-specific HUD reading only exists for a few games. Visual activity is game-agnostic.

How (2 frames/s, decoded by game.frames.scan_parallel: NVDEC when available, reference frames only):
  * each frame's luma plane is shrunk to 160x90 (INTER_AREA averages away compression noise)
  * motion[t]  = mean |Y_t - Y_(t-1)|                      (0..255; camera + on-screen movement)
  * change[t]  = 0.5 * L1 distance of 32-bin luma histograms (0..1; content swap, not just movement)
  * A hard cut / screen swap (kill cam, death screen, scoreboard, respawn) changes the histogram a
    lot; a fast camera flick moves pixels but keeps a similar histogram. So a "scene cut" needs
    BOTH high change and high motion, which separates the two far better than a motion threshold.
  * Scores are relative to a ROLLING baseline (median + MAD over 120 s), so a constantly busy
    game doesn't saturate the signal and a calm one doesn't hide its spikes.

Expected limitations (to be checked on real footage, results go in docs/RESULTS.md): menus and loading screens also produce cuts, and
spectating or replays look like activity. That is why this signal has a modest weight and why
the vision check (verify.py) exists.
"""

from __future__ import annotations

import hashlib
import json
import threading

import numpy as np
from scipy.ndimage import median_filter

from .base import ReasonEvent, Signal, SignalResult, SignalUnavailable, clip01, local_peaks, register

FPS = 2  # matches game.frames.FPS
SMALL = (160, 90)
HIST_BINS = 32
BASELINE_S = 120
CUT_CHANGE = 0.35  # histogram change that counts as a content swap
CUT_MIN_MOTION_Z = 2.0


def _robust_z(x: np.ndarray, window: int) -> np.ndarray:
    """(x - rolling median) / rolling MAD-sigma; window in samples."""
    if len(x) == 0:
        return x
    w = min(window | 1, (len(x) // 2) * 2 + 1)  # odd, not longer than the series
    med = median_filter(x, size=w, mode="nearest")
    mad = median_filter(np.abs(x - med), size=w, mode="nearest")
    floor = max(1e-3, 0.05 * float(np.median(x)) if np.median(x) > 0 else 1e-3)
    return (x - med) / np.maximum(1.4826 * mad, floor)


def measure(frames_iter) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame (t, motion, change) from an iterable of game.frames.Frame (sorted by t)."""
    import cv2

    ts, motion, change = [], [], []
    prev_small = prev_hist = None
    for fr in frames_iter:
        small = cv2.resize(fr.y, SMALL, interpolation=cv2.INTER_AREA).astype(np.int16)
        hist = np.bincount((small >> 3).ravel(), minlength=HIST_BINS).astype(np.float64)
        hist /= max(1.0, hist.sum())
        if prev_small is None:
            m, c = 0.0, 0.0
        else:
            m = float(np.mean(np.abs(small - prev_small)))
            c = 0.5 * float(np.abs(hist - prev_hist).sum())
        ts.append(fr.t)
        motion.append(m)
        change.append(c)
        prev_small, prev_hist = small, hist
    return np.array(ts), np.array(motion), np.array(change)


def score(ts: np.ndarray, motion: np.ndarray, change: np.ndarray, n: int) -> tuple[np.ndarray, list[ReasonEvent], dict]:
    """Per-second motion score 0..1 plus scene-cut / motion-burst reason events."""
    if len(ts) < 4:
        return np.zeros(n), [], {"frames": int(len(ts))}
    z = _robust_z(motion, BASELINE_S * FPS)
    cut = (change >= CUT_CHANGE) & (z >= CUT_MIN_MOTION_Z)
    frame_score = 0.75 * clip01((z - 1.0) / 4.0) + 0.25 * cut.astype(np.float64)
    sec = np.clip(ts.astype(int), 0, n - 1)
    scores = np.zeros(n)
    np.maximum.at(scores, sec, frame_score)
    events: list[ReasonEvent] = []
    cut_secs = sorted(set(int(s) for s in sec[cut]))
    last = -10
    for s in cut_secs:  # one event per burst of cuts
        if s - last >= 3:
            events.append(ReasonEvent(float(s), "scene_cut", None, 1.0))
        last = s
    for i in local_peaks(scores, 0.6, 8):
        events.append(ReasonEvent(float(i), "motion_burst", f"{float(np.max(z[sec == i], initial=0)):.1f}σ",
                                  float(scores[i])))
    meta = {"frames": int(len(ts)), "cuts": len(cut_secs),
            "motion_median": round(float(np.median(motion)), 2)}
    return scores, sorted(events, key=lambda e: e.t), meta


@register
class MotionSignal(Signal):
    name = "motion"
    version = 1
    cacheable = True

    def check(self, ctx) -> None:
        src = ctx.project.source_path
        if src is None or not src.is_file():
            raise SignalUnavailable("no source video")
        if not ctx.meta.get("width") or not ctx.meta.get("height"):
            raise SignalUnavailable("audio-only source")

    def cache_key(self, ctx) -> str:
        st = ctx.project.source_path.stat()
        payload = {"v": self.version, "src": st.st_size, "mtime": int(st.st_mtime), "n": ctx.n}
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def compute(self, ctx) -> SignalResult:
        from ...game import frames

        lock = threading.Lock()
        collected: dict[int, list] = {}

        def make_worker(i: int, _start: float, _end: float):
            buf: list = []
            with lock:
                collected[i] = buf

            def handle(fr) -> None:
                # Keep only what's needed (the luma plane is up to 1080p: shrink right away).
                import cv2

                buf.append(_Small(fr.t, cv2.resize(fr.y, SMALL, interpolation=cv2.INTER_AREA)))
            return handle

        import os

        workers = int(os.environ.get("SHARKYCLIPS_GAME_WORKERS") or 0) or None  # hardware profile
        frames.scan_parallel(str(ctx.project.source_path), ctx.duration, int(ctx.meta["width"]),
                             int(ctx.meta["height"]), make_worker, workers=workers)
        ordered = [f for i in sorted(collected) for f in collected[i]]
        ordered.sort(key=lambda f: f.t)
        ts, motion, change = measure(ordered)
        scores, events, meta = score(ts, motion, change, ctx.n)
        return SignalResult(self.name, scores, events, meta)


class _Small:
    """A Frame-like holder with an already-shrunk luma plane (measure() resizes again: no-op cost)."""

    __slots__ = ("t", "y")

    def __init__(self, t: float, y: np.ndarray):
        self.t, self.y = t, y
