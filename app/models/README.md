# app/models: downloaded AI models

Everything here is downloaded, never committed (see the root `.gitignore`). Copying the
`ai-clipper` folder to another PC copies the models too, so they don't need to be downloaded again.
`scripts\setup.ps1 -Models` fetches whatever is missing.

| Folder | Model | Size (PC A) | Used for |
|---|---|---|---|
| `huggingface/` | Whisper large-v3-turbo (faster-whisper / CTranslate2) | ~1.6 GB | Transcription and word timings |
| `ollama/` | qwen3-vl:4b (Q4_K_M) | ~3.3 GB (folder ~5 GB incl. manifests/cache) | Stage C vision check |
| `ced-mini/` | CED-mini audio tagger (ONNX) | ~10 MB | Laughter / shouting signal |
| `yunet/` | YuNet face detector (ONNX) | <1 MB | Facecam detection |

Paths are set by `app/backend/aiclipper_env.py` (`HF_HOME`, `OLLAMA_MODELS`, `SHARKYCLIPS_MODELS`).
The Ollama models are served by the app's private Ollama server (port 11435), not the system one.
