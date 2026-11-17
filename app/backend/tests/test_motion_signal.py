"""ai-clipper: the motion / scene-change signal.

Smoke tests on generated video (NOT an accuracy test: real-footage results are in docs/RESULTS.md).
"""

import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from sharkyclips.highlights.signals import motion


def _frames(arrs, fps=2):
    return [SimpleNamespace(t=i / fps, y=a) for i, a in enumerate(arrs)]


def test_static_video_scores_zero():
    still = np.full((90, 160), 100, np.uint8)
    ts, m, c = motion.measure(_frames([still] * 120))
    scores, events, _ = motion.score(ts, m, c, 60)
    assert scores.max() == 0
    assert events == []


def test_motion_burst_scores_high_and_camera_flick_is_not_a_cut():
    import cv2

    rng = np.random.default_rng(0)
    # Spatially smooth texture, like real frames (pure noise would look equally "moved" at any shift).
    noise = cv2.GaussianBlur(rng.normal(0, 1, (90, 160)), (0, 0), 4)
    base = np.clip(128 + 60 * noise / noise.std(), 0, 255).astype(np.uint8)
    arrs = []
    for i in range(240):  # 120 s at 2 fps
        if 120 <= i < 140:  # 60-70 s: the scene shifts sideways fast (camera flick: same content)
            arrs.append(np.roll(base, 6 * (i - 119), axis=1))
        else:  # small jitter elsewhere
            arrs.append(np.roll(base, i % 2, axis=1))
    ts, m, c = motion.measure(_frames(arrs))
    scores, events, meta = motion.score(ts, m, c, 120)
    assert scores[60:70].max() > 0.5
    assert scores[:50].max() < 0.2 and scores[80:].max() < 0.2
    assert not any(e.code == "scene_cut" for e in events), "a camera pan must not be a scene cut"


def test_hard_cut_is_detected():
    dark = np.full((90, 160), 30, np.uint8)
    bright = np.full((90, 160), 220, np.uint8)
    rng = np.random.default_rng(1)
    arrs = [np.clip(dark.astype(int) + rng.integers(0, 3, dark.shape), 0, 255).astype(np.uint8)
            for _ in range(200)]
    arrs[100:] = [np.clip(bright.astype(int) + rng.integers(0, 3, dark.shape), 0, 255).astype(np.uint8)
                  for _ in range(100)]
    ts, m, c = motion.measure(_frames(arrs))
    _scores, events, meta = motion.score(ts, m, c, 100)
    cuts = [e.t for e in events if e.code == "scene_cut"]
    assert cuts == [50.0]
    assert meta["cuts"] == 1


@pytest.mark.requires_ffmpeg
def test_signal_on_generated_video(tmp_path):
    """End to end through ffmpeg decoding: calm 40 s, 10 s of busy test pattern, calm 40 s."""
    src = tmp_path / "v.mp4"
    lavfi = ("color=c=gray:s=640x360:r=30:d=40[a];testsrc2=s=640x360:r=30:d=10[b];"
             "color=c=gray:s=640x360:r=30:d=40[c];[a][b][c]concat=n=3:v=1:a=0[v]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", lavfi,
                    "-map", "[v]", "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    project = SimpleNamespace(source_path=src)
    ctx = SimpleNamespace(project=project, duration=90.0, n=90, meta={"width": 640, "height": 360})
    res = motion.MotionSignal().compute(ctx)
    assert res.scores[40:50].max() > 0.5
    assert res.scores[:38].max() < 0.3 and res.scores[53:].max() < 0.3
    cut_times = [e.t for e in res.events if e.code == "scene_cut"]
    assert any(39 <= t <= 41 for t in cut_times) and any(49 <= t <= 51 for t in cut_times)
