# app/database

Currently **unused**. The app (from SharkyClips) does not use SQLite: each project is a folder
in `data/projects/<project-id>/` with JSON files, which is also the project history:

| File | Contents |
|---|---|
| `meta.json` | Source (file or link, title, channel, quality), duration, resolution, created/updated |
| `transcript.json` | Whisper transcript with word timings |
| `highlights.json` | Candidates: signals, category, confidence, engagement, vision-check verdict, phases |
| `review.json` | Your choices (Pro mode): accepted/rejected clips, per-clip edits, render options, facecam, hooks, music |
| `easy.json` | The same for Easy mode |
| `clips/` | Rendered 9:16 MP4s (created when a clip is rendered) |

App settings are in `data/app/config.json`; the job queue in `data/projects/_queue.json`.
This folder is kept so the layout in the root README stays valid. A database would only be
added if a real need (for example search across many projects) shows up.
