# Measured results

Only numbers that were actually measured are in this file. Each entry says on what input.

## Machine (PC A), detected 2026-11-17

| | |
|---|---|
| CPU | AMD Ryzen 7 250 w/ Radeon 780M, 8 cores / 16 threads (the brief said "Ryzen 7 7435HS") |
| RAM | 24 GB DDR5-5600 (the brief said 16 GB) |
| GPU | NVIDIA GeForce RTX 5050 Laptop, 8151 MiB, compute capability 12.0 (Blackwell), driver 621.14 (CUDA 13.5 UMD), 115 W max |
| Disk | WD SN5000S 1 TB NVMe, ~413 GB free |

## Toolchain checks

| Check | Result |
|---|---|
| FFmpeg n8.1.3 (FFmpeg-Builds, sha256 verified) `h264_nvenc` | 10 s of 1080×1920 test pattern: 1.0 s (libx264: 1.72 s). Output valid: h264, 1080×1920, 300 frames. |
| NVDEC (`-hwaccel cuda`) | works |
| CTranslate2 4.9.0 on sm_120 | `float16` and `int8_float16` on CUDA: OK |

## Pipeline on the synthetic demo stream (6:04, 1280×720 30 fps)

Input: `app/docs/demo/make_demo.py` (SharkyClips' synthetic stream: drawn "game", TTS voice).
**Synthetic: this measures that the pipeline runs and how fast, NOT detection accuracy.**

| Stage | Time | Peak VRAM | Notes |
|---|---|---|---|
| Transcription, Whisper large-v3-turbo float16 (GPU) | 14 s (25.1× realtime) | 3636 MiB | Whisper unloaded afterwards (VRAM back to idle). First run also downloaded the model: 5:32 wall. |
| Highlights, all signals | 37.4 s | – | laughter (CED-mini, CPU) 35.2 s (incl. first-run model download), **motion (NVDEC) 2.2 s**, loudness/keywords/speech < 0.1 s |
| Vision check, qwen3-vl:4b, 6 frames × 3 candidates | 26.9 s (~9 s/candidate incl. frame extraction and model load) | 5972 MiB total | 0 errors |

## Vision model selection (scripts/bench_vlm.py, 6 frames, same prompt)

Sanity probes with a known answer (NOT an accuracy test): static scene → no/unclear,
settings menu → no/unclear, "TRIPLE KILL" banner + kill feed → yes / multi_kill.

| Model (Q4_K_M, Ollama 0.33.2) | Probes | s/candidate (warm) | VRAM for the model |
|---|---|---|---|
| qwen2.5vl:3b | 4/6: never saw the banner ("a red circle"), called green frames "black" | 3.2 | 4647 MiB |
| **qwen3-vl:4b (chosen)** | **6/6**, cites the right frames | 4.6 | 5239 MiB |

Found during benchmarking and fixed:
- 6 frames ≈ 6.5k prompt tokens: Ollama's default 4096 context overflows → `num_ctx` sized per request.
- Ollama 0.33.2 + qwen3-vl + `think:false` + JSON schema returns the answer in `thinking` → accepted from there.
- On real frames the model answers `-1` for "no action" → accepted as "no boundary suggestion".
- The model saw "ELIMS 4 → 6" but answered "no": the prompt now says HUD counter changes are evidence.
  After this change the 3 probes still pass (no new hallucination on empty frames).

## Tests

- Upstream suite on the unmodified import (3e639e7) on this PC: all pass except 2 order-dependent
  tests (`test_web.py::test_projects_empty`, `test_pubqueue_status.py::test_browser_posts_land_in_the_library_and_the_digest`),
  which also fail identically on the pristine code; both pass when run alone.
- ai-clipper tests (motion, scoring, verify, profiles, data-root isolation): 33 passed.
- 2026-11-18 after Phase 2a (link preview): 714 passed, 1 skipped, the same 2 known failures.
- 2026-11-18 after Phase 2b (publishing gate): 722 passed, 1 skipped, 3 failed = the 2 known ones +
  `test_client_upload.py::test_shipped_built_in_client_is_a_valid_desktop_client`, which checks
  that the upstream OAuth client ships (it was removed on purpose). That test was rewritten to
  check the opposite and passes, run on its own with `test_outbound_gate.py`; the full suite was
  not re-run after that one-test change. (The Phase 2b commit message says "the same 2" failures:
  that is wrong, this entry is the correct record.)
- 2026-11-18 after the offline clipper: 734 passed, 1 skipped, 3 failed = the 2 known ones +
  `test_no_window.py` (my nvidia-smi call lacked the hidden-window flag). Fixed; that test,
  `test_offline_clip.py` (12) and `test_aiclipper_env.py` then passed (21 total); the full suite
  was not re-run after this one-line fix.

## Link import (2026-11-18, PC A, home internet)

| Check | Result |
|---|---|
| Preview (`/api/probe`) of a public YouTube video ("Me at the zoo", 0:19) | 3.4–3.9 s; title, channel, thumbnail, qualities (240p only) correct |
| Preview of a 10:35 video (Big Buck Bunny 60fps 4K) | 7 qualities listed, 360p ≈ 91 MB … 1080p60 ≈ 591 MB (estimates from yt-dlp metadata, not measured downloads) |
| Import of the 0:19 video at 240p | 11 s from request to audio extracted; transcription etc. started on their own |
| Duplicate check | Same video via `youtu.be/...` short link → "already a project" |
| Cancel during download (10:35 video, 360p) | Stopped at 1.2%; partial files and the project folder removed |

## Setup scripts (2026-11-18, PC A)

| Check | Result |
|---|---|
| `scripts\check-hardware.ps1` | Profile `rtx5050-laptop`; NVENC 10 s of 1080×1920 in 0.88 s; Whisper sees 1 CUDA device (float16, int8_float16, bfloat16 …) |
| `scripts\setup.ps1` on the existing install | All 5 steps OK, nothing re-downloaded |
| `scripts\setup.ps1` on a fresh clone (package cache + FFmpeg zip linked, Python downloaded) | First try **failed**: Python download timed out 3× (uv's 30 s default). After raising the timeout: OK in 217 s (Python download alone 3 min 21 s) |
| Found while testing | uv placed a `python3.12.exe` shim in `%USERPROFILE%\.local\bin` during the 2026-11-17 setup (outside ai-clipper). `setup.ps1` now passes `--no-bin`. |

Not yet tested: `setup.ps1` on PC B, and `-Models` / `-Dev` / `-Offline` end to end.

## Offline clipper on the synthetic demo (2026-11-18, PC A)

Command: `scripts\clip.ps1 data\eval\neon-drift-demo.mp4 -Top 7 -Truth data\eval\neon-drift-demo.truth.txt`.
Truth = the 7 scripted events of `app/docs/demo/make_demo.py` (explosion −2 s … +7 s), written from
the generator script, not from results. **Synthetic video, 7 events: this shows the pipeline and the
measurement work; it says little about accuracy on real gameplay.** 3 runs, identical detections.

| Stage (run 3) | Time (s) | Peak VRAM (MiB, whole GPU) | Peak RAM (MiB, app + Ollama) | CPU avg/peak % |
|---|---|---|---|---|
| import | 0.1 | – | – | – |
| transcribe (large-v3-turbo, CUDA float16) | 6.2 | 3901 | 1743 | 14 / 36 |
| highlights, baseline | 1.0 | 1087 | 1018 | 23 / 28 |
| highlights, ai-clipper | 2.9 | 1527 | 1581 | 34 / 54 |
| vision check, 6 candidates (qwen3-vl:4b) | 54.5 | 6337 | 6240 | 19 / 47 |
| facecam detection | 3.7 | 1098 | 1263 | 71 / 93 |
| render 6 clips (NVENC, 1080×1920, subtitles) | 21.2 (2.5–2.9 s/clip) | 3250 | 1383 | 34 / 61 |
| **total** | **90.1** for a 6:04 video | | | |

Idle VRAM before the run: 998 MiB (other programs).

| | Detections | Correct | False pos. | Missed | Precision | Recall | Mean timing error | Whole event inside clip |
|---|---|---|---|---|---|---|---|---|
| Baseline (SharkyClips ranking, no motion, no vision) | 7 | 6 | 1 | 1 | 0.86 | 0.86 | 1.9 s | 6/6 |
| ai-clipper | 6 | 6 | 0 | 1 | 1.00 | 0.86 | 1.9 s | 6/6 |

What explains the difference (checked in report.json):
- The baseline's false positive (1:45–2:05) ends 1 s before the round-3 event (2:06–2:15): a
  mistimed detection of that event. ai-clipper produced no candidate there. Both miss that event.
- The vision check answered "yes" for all 6 ai-clipper candidates: on this video it filtered nothing.
  The gain comes from candidate selection with the motion signal, not from the vision model.
- Same run with `-NoVision`: 42.2 s total, identical detections and scores. On this video the
  vision check costs ~50 s and changes nothing; whether it removes false positives must be
  measured on real footage.
- Event category (ai-clipper only): 3 of 6 right (clutch, 2× multi_kill); the boss escape,
  the trick jump and the final 1v1 were all called "clutch".
- Output: 6 MP4s, H.264 1080×1920 30 fps + AAC, 24–29 s each (all within 20–60 s).
- Facecam: none found (correct: the demo's "facecam" is a logo, not a face).

Offline check (network connections of the app, its children and any `ollama` process, sampled
every 0.5 s):
- Run 1: **failed**: `ollama.exe` connected to 34.36.133.15:443 (Google Cloud). A separate test
  showed `ollama serve` 0.33.2 makes that connection at startup; with `OLLAMA_NO_CLOUD=1` it makes
  none and still answers. Fixed in `aiclipper_env.py` and `llm.ensure_server`.
- Runs 2 and 3: no connection to any non-local address. (This watches connections; it is not the
  same as running with the network cable unplugged.)
- Run 1 also left the private Ollama server running after the command ended; `clip` now stops
  the server it started.

## Pending: real recording validation (brief §15)

Not done yet. Needs a 5–10 min real gameplay recording plus the user's own list of true
highlight moments. Will report: correct detections, false positives, misses, timestamp error,
processing time, peak VRAM/RAM, CPU use, export time.
