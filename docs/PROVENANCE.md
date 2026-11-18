# Provenance: where each part of ai-clipper comes from

## Upstream sources (kept unchanged in `repo/`)

| Repo | Commit | Licence | Role |
|---|---|---|---|
| [utkualsir/sharkyclips](https://github.com/utkualsir/sharkyclips) | `cf7a5a0` (v1.2.50) | MIT | **Foundation.** Imported unmodified as the first commit of this repo (`3e639e7`), so `git diff 3e639e7 -- app/` shows every ai-clipper change. |
| [DRIRU/clippie](https://github.com/DRIRU/clippie) | `7044175` | **None** (no licence file = all rights reserved) | **Design reference only.** No Clippie code was copied. Ideas listed below were re-implemented from scratch. |

## Why SharkyClips is the foundation (audit, 2026-11-17)

Both repos were audited from source, not READMEs.

- SharkyClips: ~25k lines of backend, ~591 tests, a React UI whose 160 API calls all match real
  backend routes, Whisper with a GPU→CPU fallback chain, NVENC render with a test encode, a
  resource governor, one-model-at-a-time GPU use, MIT licence.
- Clippie: a ~3k-line prototype. Its own AUDIT.md says no detector, profile or VLM blend was
  ever checked on real gameplay. It has no licence, so its code cannot legally be reused.

## Components by origin

### From SharkyClips (MIT, modified where noted)
Ingestion (yt-dlp, local files), transcription (faster-whisper), audio signals (loudness,
laughter/shouting via CED-mini, reaction keywords, speech), HUD OCR (Delta Force, League of
Legends), candidate picking + NMS + word-boundary refinement, 9:16 rendering (split/fit/crop,
NVENC), subtitles (libass), governor (silent/balanced/turbo), job queue, React UI.

Modified for ai-clipper:
- `config.py`: `models_dir()` + `SHARKYCLIPS_WORKSPACE`; hardware-profile layer in `load()`; new settings keys.
- `llm.py`: private Ollama server (`AICLIPPER_OLLAMA_URL`, own port 11435, models in `app/models/ollama`).
- `startup.py`: never edits the Windows Startup folder on its own.
- `highlights/modes.py`: `motion` weight; user-configurable `signal_weights`.
- `highlights/pipeline.py`: calls `scoring.apply`.
- `web/app.py`: `verify` job in the chain (highlights → verify → analyze), `/api/projects/{id}/verify`.
- `cli.py`: imports `aiclipper_env`; `verify` command; confidence/engagement columns.
- Six model-path call sites now use `config.models_dir()`.
- `ingest.py`: `probe()` link preview (qualities, sizes, duplicate check), chosen download quality,
  cancel that stops the transfer and removes partial files.
- `languages.py`, `frontend/src/i18n/index.tsx`: English default (upstream: Turkish).
- Publishing (YouTube API/OAuth, TikTok API + browser, Instagram/Facebook, Telegram) and the
  git-pull updater: code kept, but every network entry point calls `outbound.require()`, which
  refuses unless `AICLIPPER_PUBLISHING=1` (set only by the test suite). The upstream author's
  `google_client.json` (their Google OAuth client) was removed from the app; it remains in the
  history of the unmodified import commit `3e639e7`, as it does in the public upstream repo.
- Frontend: the publishing screens, badges and buttons render only when the server reports
  `publishing: true` (`meta.whenPublishing`).

### New in ai-clipper (written for this project)
| File | What |
|---|---|
| `app/backend/aiclipper_env.py` | Keeps every file inside `ai-clipper/` (paths derived from the file location). |
| `app/backend/sharkyclips/highlights/signals/motion.py` | Game-agnostic motion + scene-change signal (rolling robust z-score, histogram-change cut test). |
| `app/backend/sharkyclips/scoring.py` | Separate confidence / engagement scores, categories, event phases (Stage D). |
| `app/backend/sharkyclips/verify.py` | Stage C vision-model verification (Ollama, structured JSON). |
| `app/backend/sharkyclips/hwprofile.py` + `app/config/profiles/*.json` | Hardware profiles (PC A RTX 5050, PC B RTX 5060, generic, CPU). |
| `app/backend/sharkyclips/offline_clip.py` + `scripts/clip.ps1` | One-command offline clipper with per-stage measurements, network watch and baseline-vs-ai-clipper scoring against a truth file. |
| `app/backend/sharkyclips/outbound.py` | Offline-first gate for uploads, posting and online updates. |
| `app/frontend/src/components/LinkPreview.tsx` | Link preview before download (thumbnail, quality, duplicate). |
| `scripts/bench_vlm.py` | Vision-model benchmark + sanity probes. |

### Ideas taken from Clippie's design (re-implemented, no code copied)
- Verifying detector candidates with a VLM on a few frames instead of the whole video.
  Changes: frames chosen symmetric around the peak (Clippie's sampler dropped frames after
  the peak); the detector's label is NOT shown to the model (avoids confirmation bias); `-1`
  for "no action"; asymmetric fusion into a separate confidence score.
- Storing a per-candidate score breakdown so weights can be tuned against labels (`score_inputs`).
- (Planned) OCR cost gates/cache and a rule-based on-screen banner matcher for new games.
