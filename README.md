# ADK Live Translator

Real-time audio translation app powered by ADK Gemini Live API Toolkit. Speak in any language and get immediate audio translation in your chosen target language.

Supports 97 languages including English, Japanese, Chinese, Spanish, French, German, Portuguese, Korean, Hindi, Arabic, and many more.

![Demo](demo.gif)

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- [Gemini API key](https://aistudio.google.com/apikey)

## Setup

```bash
uv sync
```

Set your Gemini API key in `app/.env`:

```
GOOGLE_API_KEY=your-api-key
```


## Custom Glossary

You can provide a custom glossary in `app/dict.csv` to ensure specific terms are always translated consistently. Each line is a comma-separated pair of source and target terms:

```csv
Kubernetes,クバネティス
Cloud Run,クラウドラン
Gemini,ジェミニ
```

## Run

```bash
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 and click **Start Audio** to begin translating.

## Architecture

Uses ADK's 4-phase bidi-streaming lifecycle over WebSocket:

1. **App init** — FastAPI server, Agent (`gemini-3.1-flash-live-preview`), Runner, SessionService
2. **Session init** — RunConfig with AUDIO modality, transcription, session resumption
3. **Active streaming** — Concurrent upstream (mic → LiveRequestQueue) and downstream (run_live → WebSocket) tasks
4. **Termination** — `LiveRequestQueue.close()` on disconnect

## Model

Uses `gemini-3.1-flash-live-preview` with the Gemini API (`generativelanguage.googleapis.com`). This is a native audio model supporting real-time audio input/output with transcription. It only accepts `realtime_input` (audio blobs), not `client_content` (turn-based text).

## Deployment to Cloud Run

### 1. Prerequisites

- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) (`gcloud`) installed and configured
- A Google Cloud project with Cloud Run API enabled

### 2. Configure Environment

Set your Gemini API key in `app/.env`:

```
GOOGLE_API_KEY=your-api-key
```

Export it before deploying:

```bash
set -a
source app/.env
set +a
```

### 3. Deploy

```bash
gcloud run deploy live-translation \
  --source . \
  --project YOUR_PROJECT \
  --region us-central1 \
  --allow-unauthenticated \
  --timeout 3600 \
  --min-instances 1 \
  --max-instances 1 \
  --set-env-vars "GOOGLE_API_KEY=${GOOGLE_API_KEY}"
```

Key flags:
- `--timeout 3600` — Live API sessions can be long-lived (up to 1 hour)
- `--min-instances 1` — avoids cold start latency for WebSocket connections
- `--max-instances 1` — sufficient for demo; increase for production


## ADK/SDK Compatibility Patches

ADK 1.28.1 + genai SDK 1.70 require patches in `app/main.py` to work with `gemini-3.1-flash-live-preview` on the Gemini API:

1. **Remove Vertex AI env vars** — The genai SDK auto-detects `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` and routes to `aiplatform.googleapis.com`. We pop these env vars to force Gemini API key routing via `generativelanguage.googleapis.com`. (SDK behavior, not an ADK bug.)

2. **API version `v1alpha` → `v1beta`** — ADK hardcodes `Gemini._live_api_version = "v1alpha"` for live connections, but `gemini-3.1-flash-live-preview` only exists on `v1beta`. Tracked in [google/adk-python#5075](https://github.com/google/adk-python/issues/5075), fix in [PR #5076](https://github.com/google/adk-python/pull/5076).

3. **Audio-only (no text input)** — The model rejects `client_content` messages with `1007 invalid argument`. ADK also replays session history as `client_content` on reconnect, so text input is removed entirely. Tracked in [google/adk-python#5018](https://github.com/google/adk-python/issues/5018) and [#5075](https://github.com/google/adk-python/issues/5075), fix in [PR #5076](https://github.com/google/adk-python/pull/5076).


### Known Issue: Session resumption / transparent reconnection

The Live API disconnects after ~10 minutes. ADK has session resumption plumbing (stores handles from `session_resumption_update` events), but the reconnection loop in `base_llm_flow.py` never iterates — both exception handlers re-raise instead of continuing the `while True` loop. Additionally, `goAway` messages from the server are silently dropped.

Tracked upstream: [google/adk-python#4996](https://github.com/google/adk-python/issues/4996)
