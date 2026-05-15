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

FastAPI bridges a browser WebSocket to one Gemini Live API session per connection via `google-genai`'s `client.aio.live.connect(...)`:

1. **Session init** — On WebSocket connect the server reads a JSON setup message (`{glossary: [...]}`) sent by the browser as the first frame, builds a per-connection system instruction that embeds the glossary, and opens a `LiveConnectConfig` with AUDIO modality and input/output transcription.
2. **Active streaming** — Concurrent upstream (mic → `session.send_realtime_input(audio=...)`) and downstream (`session.receive()` → WebSocket) tasks. `session.receive()` is per-turn, so the downstream loop wraps it in an outer loop to span multiple turns. The server translates each `LiveServerMessage` into a small camelCase JSON envelope the frontend expects (`turnComplete`, `inputTranscription`, `outputTranscription`, `content.parts[]`, `usageMetadata`). The frontend swaps `target` → `transcription` on incoming output-transcription text before rendering.
3. **Termination** — When either side finishes (client disconnects or live session ends), `asyncio.wait(FIRST_COMPLETED)` cancels its partner and the `async with` exits, closing the upstream connection to Google.

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
- `--timeout 3600` — Live API sessions can be long-lived (up to 1 hour)
- `--min-instances 1` — avoids cold start latency for WebSocket connections
- `--max-instances 1` — sufficient for demo; increase for production


## SDK Compatibility Note

`app/main.py` pops `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` before importing the agent. The genai SDK auto-detects these and would otherwise route requests to `aiplatform.googleapis.com`; clearing them forces Gemini API key routing via `generativelanguage.googleapis.com`.
