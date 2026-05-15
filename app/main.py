"""FastAPI application for real-time live translation using the Gemini Live API."""

import asyncio
import base64
import json
import logging
import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables from .env file BEFORE constructing the genai client.
load_dotenv(Path(__file__).parent / ".env")

# Ensure non-Vertex AI mode for Gemini API key auth.
# These env vars cause the SDK to route through aiplatform.googleapis.com.
os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
os.environ.pop("GOOGLE_CLOUD_LOCATION", None)

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from translator_agent import (  # noqa: E402
    LANGUAGES,
    MODEL,
    POPULAR_LANGUAGES,
    build_system_instruction,
    load_default_glossary,
)

MAX_GLOSSARY_ENTRIES = 1000  # safety cap on per-session glossary length
SETUP_TIMEOUT_SEC = 5  # how long to wait for the client's setup message
AUTHOR = "live_translator"  # constant author tag echoed in every server frame

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress Pydantic serialization warnings emitted by the genai SDK.
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

app = FastAPI()

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


@app.get("/")
async def root():
    """Serve the index.html page."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/languages")
async def get_languages():
    """Return available languages with popular ones highlighted."""
    return {"languages": LANGUAGES, "popular": POPULAR_LANGUAGES}


@app.get("/api/glossary/defaults")
async def get_default_glossary():
    """Return the seed glossary baked into the image (used when localStorage is empty)."""
    entries = load_default_glossary()
    return {
        "pairs": [
            {"source": s, "target": t, "transcription": d} for s, t, d in entries
        ]
    }


def _parse_setup(raw: str) -> list[tuple[str, str, str]]:
    """Parse the client's setup message into validated glossary triples."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    entries: list[tuple[str, str, str]] = []
    for entry in (data.get("glossary") or [])[:MAX_GLOSSARY_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        src = (entry.get("source") or "").strip()
        tgt = (entry.get("target") or "").strip()
        if not src or not tgt:
            continue
        disp_raw = entry.get("transcription")
        disp = disp_raw.strip() if isinstance(disp_raw, str) and disp_raw.strip() else tgt
        entries.append((src, tgt, disp))
    return entries


def _envelope_from(msg: types.LiveServerMessage) -> dict | None:
    """Translate a LiveServerMessage into the camelCase JSON shape `app.js` expects.

    Returns None for messages the client doesn't care about (setup acks, go_away,
    session-resumption updates) so the caller can skip them.
    """
    out: dict = {}

    sc = msg.server_content
    if sc:
        if sc.turn_complete:
            out["turnComplete"] = True
        if sc.interrupted:
            out["interrupted"] = True
        if sc.input_transcription:
            out["inputTranscription"] = {
                "text": sc.input_transcription.text or "",
                "finished": bool(sc.input_transcription.finished),
            }
        if sc.output_transcription:
            out["outputTranscription"] = {
                "text": sc.output_transcription.text or "",
                "finished": bool(sc.output_transcription.finished),
            }
        if sc.model_turn and sc.model_turn.parts:
            parts = []
            for p in sc.model_turn.parts:
                pj: dict = {}
                if p.text is not None:
                    pj["text"] = p.text
                if p.thought:
                    pj["thought"] = True
                if p.inline_data and p.inline_data.data is not None:
                    pj["inlineData"] = {
                        "mimeType": p.inline_data.mime_type or "",
                        "data": base64.b64encode(p.inline_data.data).decode("ascii"),
                    }
                if pj:
                    parts.append(pj)
            if parts:
                out["content"] = {"role": "model", "parts": parts}
                # Streaming chunks are partial; the final frame carries turn_complete.
                if not sc.turn_complete:
                    out["partial"] = True

    if msg.usage_metadata:
        out["usageMetadata"] = msg.usage_metadata.model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )

    if not out:
        return None
    out["author"] = AUTHOR
    return out


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    source: str = "en",
    target: str = "ja",
) -> None:
    """WebSocket endpoint bridging browser audio to a Gemini Live session."""
    logger.debug(
        f"WebSocket connection request: user_id={user_id}, session_id={session_id}, "
        f"source={source}, target={target}"
    )
    await websocket.accept()
    logger.debug("WebSocket connection accepted")

    # Wait for the client's setup message (carries the per-session glossary).
    # Falls back to the on-disk default glossary if the client doesn't send one
    # within SETUP_TIMEOUT_SEC (older clients, network hiccups).
    glossary_entries: list[tuple[str, str, str]] | None = None
    try:
        setup_raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=SETUP_TIMEOUT_SEC
        )
        glossary_entries = _parse_setup(setup_raw)
        logger.debug("Setup received: %d glossary entries", len(glossary_entries))
    except asyncio.TimeoutError:
        logger.warning(
            "No setup message within %ds; using default glossary.", SETUP_TIMEOUT_SEC
        )
    except WebSocketDisconnect:
        logger.debug("Client disconnected before sending setup")
        return

    system_instruction = build_system_instruction(source, target, glossary_entries)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(parts=[types.Part(text=system_instruction)]),
    )

    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            logger.debug("Live session opened with model=%s", MODEL)

            async def upstream_task() -> None:
                """Forward browser audio frames into the Live session."""
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            logger.debug("Upstream: client disconnected")
                            return
                        if "bytes" in message:
                            audio = message["bytes"]
                            logger.debug("Received audio chunk: %d bytes", len(audio))
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    mime_type="audio/pcm;rate=16000", data=audio
                                )
                            )
                        elif "text" in message:
                            logger.debug("Ignoring text message (audio-only)")
                except WebSocketDisconnect:
                    logger.debug("Upstream: client disconnected")

            async def downstream_task() -> None:
                """Forward model events back to the browser as JSON envelopes.

                The SDK's `session.receive()` returns per-turn (it `break`s after
                `turn_complete`), so we wrap it in an outer loop and only exit
                when the underlying session itself ends.
                """
                while True:
                    saw_message = False
                    async for msg in session.receive():
                        saw_message = True
                        envelope = _envelope_from(msg)
                        if envelope is None:
                            continue
                        payload = json.dumps(envelope)
                        logger.debug("[SERVER] Event: %s", payload)
                        await websocket.send_text(payload)
                    if not saw_message:
                        logger.debug("Live session ended (no more turns)")
                        return

            up = asyncio.create_task(upstream_task())
            down = asyncio.create_task(downstream_task())
            done, pending = await asyncio.wait(
                {up, down}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except WebSocketDisconnect:
        logger.debug("Client disconnected normally")
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error in streaming tasks")
    finally:
        logger.debug("WebSocket handler exiting")
