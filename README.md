# Live Translator

Real-time audio translation app powered by Gemini Live API. Speak in any language and get immediate audio translation in your chosen target language.

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

Pin specific terms to a fixed translation so the model always renders them the same way. **The glossary is per browser** — it's stored in your browser's `localStorage` and sent to the server only when you start a session. Different visitors can run different glossaries at the same time without affecting each other; nothing is persisted server-side.

1. Click **Glossary** in the header. On first visit the modal seeds itself from the default glossary baked into the app (`app/dict.csv`). Entries are shown in a three-column table: **Source / Pronunciation / Transcript**.
2. Click **Choose .csv file** and pick a UTF-8 CSV (max 256 KB, max 1000 entries). Each line is `source,target[,transcription]`:

   ```csv
   Kubernetes,クバネティス,Kubernetes
   Cloud Run,クラウドラン,Cloud Run
   Gemini,ジェミニ,Gemini
   Vertex AI,バーテックスエーアイ,Vertex AI
   ```

   The optional **third column** is a display override. The model still pronounces the term using `target` (so the audio sounds right), but the on-screen transcript renders `transcription` in place of `target`. Useful for proper nouns where you want a phonetic pronunciation but a Latin display label. When omitted, the transcript shows `target` as-is. The replacement happens client-side, so it never affects what the model emits — only what you see.

3. Click **Load & replace**. The CSV is parsed in the browser and stored locally. Use **Reset to defaults** to discard your customisations and re-fetch the seed glossary from the server.
4. The new entries take effect on the **next** session — click **Start Audio** again, or change languages, to open a fresh WebSocket. Live sessions keep the glossary they started with.

Status feedback appears below the entry count: green for success, red for parse errors (a line missing the comma, file too large, etc.) — in the error case the previous glossary stays in place.

### Changing the default glossary

Edit `app/dict.csv` and redeploy. The endpoint `GET /api/glossary/defaults` returns those defaults. Browsers that already have a cached glossary in `localStorage` will keep using it — they need to either click **Reset to defaults** in the modal, or the app needs to bump the storage key (`GLOSSARY_KEY` in `app/static/js/app.js`) to force a one-time re-seed on next load.

## Run

```bash
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 and click **Start Audio** to begin translating.

## Architecture

FastAPI bridges one browser WebSocket to a series of Gemini Live API sessions via `google-genai`'s `client.aio.live.connect(...)`. The browser WS lives for the lifetime of the user's tab; upstream Live sessions are opened, expire, and reopened underneath it without the browser noticing.

1. **Browser session start** — The browser opens a WebSocket to `/ws/{user_id}/{session_id}` and sends a JSON setup frame (`{glossary: [...]}`). The server parses it and builds a per-connection system instruction that embeds the glossary.
2. **Upstream session loop** — A background coroutine repeatedly opens `client.aio.live.connect(model, config)` with `LiveConnectConfig(response_modalities=[AUDIO], input/output_audio_transcription=…, session_resumption=…)`. Each open passes any stored resumption handle for `session_id` so the model picks up where it left off; on each `session_resumption_update.new_handle` from the server, the latest handle is stored in-memory (keyed by `session_id`, lazily evicted after the Live API's ~10 min window). When the upstream session expires — Live API sessions cap out around 15 min and apply shorter idle timeouts — the loop simply opens another one with the freshest handle. The browser WebSocket stays open across every reopen.
3. **Audio bridge** — A separate coroutine pulls binary audio frames from the browser WS and forwards them to whichever upstream session is current via `send_realtime_input(audio=...)`. Frames that arrive during the sub-second window between upstream sessions are dropped; resumption preserves model context across the gap, so the speaker doesn't notice.
4. **Wire format** — Each `LiveServerMessage` from upstream is translated into a small camelCase JSON envelope the frontend understands (`turnComplete`, `inputTranscription`, `outputTranscription`, `content.parts[]`, `usageMetadata`). The frontend swaps `target` → `transcription` on incoming output-transcription text before rendering.
5. **Termination** — The handler only exits when the browser disconnects (or on a fatal error). Upstream sessions are torn down implicitly each time the inner `async with` exits and re-enters.

## Model

Uses `gemini-3.1-flash-live-preview` with the Gemini API (`generativelanguage.googleapis.com`). This is a native audio model supporting real-time audio input/output with transcription. The app sends audio via `send_realtime_input` (16 kHz mono PCM) and receives audio at 24 kHz.

The system instruction is built in `app/translator_agent/agent.py` as:

```
You are a real-time translator from {source} to {target}. Listen to the
incoming audio and immediately output the translated version in {target},
maintaining the speaker's original tone and urgency.

Use the following glossary for specific terms. When you hear these words,
always use the paired translation:
- <source> → <target>
...
```

Only the first two CSV columns reach the model; the third (transcript display) is purely a frontend post-processing rule.

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
- `--timeout 3600` — the browser WebSocket can hold a single conversation for up to an hour, even though the upstream Live sessions inside it cycle every ~15 min.
- `--min-instances 1` — avoids cold start latency for WebSocket connections.
- `--max-instances 1` — required as written, because session-resumption handles are kept in an in-memory dict on the server. Going multi-replica would route a browser reconnect to a different instance with no handle, defeating resumption. Use a shared store (e.g. Redis) before raising this.


## Testing

### Soak Test

`tests/test_long.py` is a long-running automated test that validates translation quality, latency, glossary behavior, and session stability over extended periods (default 1 hour).

It generates random English sentences via Gemini Flash Lite, converts them to audio with Google Cloud TTS, streams them through the translator WebSocket, transcribes the returned audio with Google Cloud STT, and verifies semantic correctness.

```bash
uv sync --extra test

# 2-minute smoke test against local server
uv run python tests/test_long.py --duration 120

# 1-hour test against Cloud Run
uv run python tests/test_long.py --url wss://YOUR_CLOUD_RUN_URL --duration 3600
```

Options:
- `--url` — WebSocket base URL (default: `ws://localhost:8000`)
- `--duration` — Test duration in seconds (default: 3600)
- `--source` / `--target` — Language pair (default: en / ja)
- `--log` — Path to JSONL metrics log (default: auto-generated `soak_YYYYMMDD_HHMMSS.jsonl`)

The test exercises session resumption and GoAway handling by running on a single persistent WebSocket for the entire duration.

#### Metrics logged per iteration

Each iteration writes a JSON line to the log file with:

| Field | Description |
|---|---|
| `first_response_sec` | Time from end of speech to first model response (typically near 0 — the model processes audio in real-time) |
| `turn_complete_sec` | Time from end of speech to `turnComplete` (user-perceived latency for the full translation) |
| `elapsed_sec` | Total iteration time including TTS, STT, and verification |
| `score` | Semantic translation quality (0-10, via Gemini Flash Lite) |
| `input_transcription_score` | Input transcription accuracy (0-10) |
| `output_transcription_score` | Output transcription accuracy (0-10) |
| `glossary_found` | Whether the glossary display term appeared in output (every 3rd iteration) |

Load the JSONL with pandas for visualization:

```python
import pandas as pd
df = pd.read_json("soak_20260515_220413.jsonl", lines=True)
```

## SDK Compatibility Note

`app/main.py` pops `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` before constructing the genai client. The SDK auto-detects these and would otherwise route requests to `aiplatform.googleapis.com`; clearing them forces Gemini API key routing via `generativelanguage.googleapis.com`.
