# Live Translator

Real-time audio translation powered by Gemini Live API. Speak in any language and hear the translation immediately. Supports 97 languages.

![Demo](demo.gif)

## Getting Started

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- [Gemini API key](https://aistudio.google.com/apikey)

### Setup

```bash
uv sync
```

Create `app/.env` with your API key:

```
GOOGLE_API_KEY=your-api-key
```

### Run

```bash
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000.

## User Guide

### Basic Usage

1. Select source and target languages from the bottom bar
2. Click **Start** to begin continuous translation (always-on mode)
3. Speak into your microphone — translations appear as text bubbles and play as audio

### Push to Talk

Toggle **Push to Talk** on the right to switch from always-on to manual control. Hold the **Hold to Talk** button (or press spacebar) to transmit, release to stop. A 1.5s audio tail is sent after release so the model's VAD can detect end-of-speech.

### Audio Settings

Click **Audio** in the header to select which microphone and speaker to use. Choices are saved in your browser and applied on the next session.

### Glossary

Click **Glossary** in the header to pin specific terms to fixed translations. The glossary is per-browser (stored in `localStorage`) and sent to the server on each new session.

Upload a UTF-8 CSV with `source,target[,transcription]` per line:

```csv
Kubernetes,クバネティス,Kubernetes
Cloud Run,クラウドラン,Cloud Run
Vertex AI,バーテックスエーアイ,Vertex AI
```

The optional third column is a display override — the model pronounces the `target` form, but the on-screen transcript shows the `transcription` form. Useful for proper nouns where you want phonetic audio but a Latin display label.

Changes take effect on the next session (click **Start** again, or change languages).

#### Changing the default glossary

Edit `app/dict.csv` and redeploy. Browsers with a cached glossary keep using it until the user clicks **Reset to defaults** in the modal.

### Connection States

The status indicator in the top-right corner shows:
- **Yellow dot / Connecting...** — WebSocket connected, model warming up
- **Green dot / Connected** — ready to translate
- **Red dot / Disconnected** — connection lost, auto-reconnects in 5s

The Start button and PTT toggle are disabled until the warmup completes.

---

## Technical Details

### Architecture

```
Browser                          Server (FastAPI)                 Gemini Live API
  |                                  |                                |
  |-- WebSocket /ws/{user}/{sid} --> |                                |
  |   (binary PCM frames)           |-- client.aio.live.connect() -->|
  |                                  |                                |
  |                                  |<-- LiveServerMessage ---------|
  |<-- JSON envelope --------------- |                                |
  |   (transcription, audio, etc.)   |                                |
```

FastAPI bridges one browser WebSocket to a series of Gemini Live API sessions. The browser WS lives for the lifetime of the user's tab; upstream Live sessions are opened, expire (~15 min), and reopened underneath it transparently.

**Connection lifecycle:**

1. Browser opens WebSocket to `/ws/{user_id}/{session_id}` and sends a JSON setup frame with the per-browser glossary
2. Server builds a system instruction embedding the glossary and language pair
3. Two background coroutines run concurrently:
   - **session_loop** opens a Gemini Live session, drains messages from it, and forwards them as JSON envelopes to the browser
   - **upstream_task** forwards binary audio frames from the browser WS to whichever upstream session is current
4. When the upstream session sends a GoAway (expiring in ~30s), the server immediately starts opening the next session in the background while continuing to drain the current one — this eliminates dead time between sessions
5. Once the old session finishes, the pre-opened session takes over seamlessly

**Wire format:** Each `LiveServerMessage` is translated into a camelCase JSON envelope the frontend understands (`turnComplete`, `inputTranscription`, `outputTranscription`, `content.parts[]`, `usageMetadata`).

**Transcription behavior:** Output transcription (the translated speech) streams in multiple partial chunks, so the UI can show word-by-word updates with a typing indicator. Input transcription (the user's spoken words) arrives as a single message with the complete text — the API does not stream partial input transcriptions, so the user's bubble appears all at once.

### Model

Uses `gemini-3.1-flash-live-preview` via the Gemini API (`generativelanguage.googleapis.com`). Audio input is 16 kHz mono PCM; output is 24 kHz PCM.

The system instruction (built in `app/translator_agent/agent.py`) tells the model to translate only the current utterance and never repeat previous translations. The glossary is embedded as `source → target` pairs with case-insensitive matching.

### GoAway Handling

Gemini Live API sessions expire after ~15 minutes. When the server receives a GoAway message:

1. A new session starts opening immediately in the background (`_open_next()`)
2. The old session continues draining — any in-progress translation completes and is forwarded to the browser
3. After the old session ends (or the GoAway deadline expires), the pre-opened session becomes the active session
4. Audio from the browser is routed to the new session with no gap

**Limitation:** If GoAway fires mid-utterance, the translation in progress may be lost. The model on the new session has no context from the previous session, so it starts fresh. In practice this affects ~1-2% of translations during long sessions.

Session resumption was intentionally removed — it caused an off-by-one translation cascade where the model would prepend the previous turn's translation to the current one. Without resumption, each session starts clean, which proved more reliable (98% pass rate vs 65% with resumption in 1-hour soak tests).

### SDK Note

`app/main.py` clears `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` before constructing the genai client. These env vars cause the SDK to route through `aiplatform.googleapis.com`; clearing them forces Gemini API key routing via `generativelanguage.googleapis.com`.

### Deployment to Cloud Run

```bash
set -a && source app/.env && set +a

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
- `--timeout 3600` — allows hour-long WebSocket conversations (upstream Live sessions cycle internally every ~15 min)
- `--min-instances 1` — avoids cold start latency
- `--max-instances 1` — session resumption handles are stored in-memory; multi-replica requires a shared store (e.g. Redis)

## Testing

### Soak Test

`tests/test_long.py` validates translation quality, latency, glossary behavior, and session stability over extended periods (default 1 hour).

It generates random English sentences via Gemini Flash Lite, converts them to audio with Google Cloud TTS, streams them through the translator WebSocket, transcribes the returned audio with Google Cloud STT, and verifies semantic correctness.

```bash
uv sync --extra test

# 2-minute smoke test against local server
uv run python tests/test_long.py --duration 120

# 1-hour test against Cloud Run
uv run python tests/test_long.py --url wss://YOUR_CLOUD_RUN_URL --duration 3600
```

Options: `--url` (WebSocket base URL), `--duration` (seconds), `--source`/`--target` (language pair), `--log` (JSONL output path).

#### Latest soak test results (1 hour, en → ja, Cloud Run)

```
Duration: 3631s | Iterations: 185 | Passed: 178/185 (96.2%) | Avg score: 9.7/10
Glossary: 34/61 (55.7%) terms matched in output

First Response (speech-end to first audio/transcript)
  avg=0.05s  p50=0.00s  p90=0.09s  p99=2.25s

Turn Complete (speech-end to full translation)
  avg=6.28s  p50=6.17s  p90=7.72s  p99=12.45s

Translation Score
  avg=9.66  p50=10.00  9-10: 95.7%

Input Transcription Score
  avg=9.96  p50=10.00  9-10: 99.5%

Output Transcription Score
  avg=9.41  p50=10.00  9-10: 88.0%
```

