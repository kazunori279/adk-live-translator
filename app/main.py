"""FastAPI application for real-time live translation using the Gemini Live API."""

import asyncio
import base64
import json
import logging
import os
import sys
import unicodedata
import warnings
from dataclasses import dataclass, field
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
    VR_MODEL,
    build_system_instruction,
    load_default_glossary,
)

MAX_GLOSSARY_ENTRIES = 1000  # safety cap on per-session glossary length
SETUP_TIMEOUT_SEC = 5  # how long to wait for the client's setup message
CONNECT_TIMEOUT_SEC = 10
RETRY_BACKOFF_INIT = 0.2
RETRY_BACKOFF_MAX = 4.0
AUTHOR = "live_translator"  # constant author tag echoed in every server frame

def _build_display_map(
    entries: list[tuple[str, str, str]],
) -> list[tuple[str, str]]:
    """Build (nfkc_target, transcription) pairs for server-side transcript replacement."""
    pairs = [
        (unicodedata.normalize("NFKC", tgt), disp)
        for src, tgt, disp in entries
        if tgt != disp
    ]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def _apply_display_map(text: str, display_map: list[tuple[str, str]]) -> str:
    """Replace glossary target strings in *text* with their display transcription."""
    if not text or not display_map:
        return text
    out = unicodedata.normalize("NFKC", text)
    for nfkc_target, transcription in display_map:
        if nfkc_target in out:
            out = out.replace(nfkc_target, transcription)
    return out


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
    return {
        "languages": LANGUAGES,
        "popular": POPULAR_LANGUAGES,
        "model": MODEL,
        "vrModel": VR_MODEL,
    }


@app.get("/api/glossary/defaults")
async def get_default_glossary():
    """Return the seed glossary baked into the image (used when localStorage is empty)."""
    entries = load_default_glossary()
    return {
        "pairs": [
            {"source": s, "target": t, "transcription": d} for s, t, d in entries
        ]
    }


@dataclass
class SetupData:
    glossary: list[tuple[str, str, str]] = field(default_factory=list)
    vr_voice_sample: bytes | None = None
    vr_consent_audio: bytes | None = None


def _parse_setup(raw: str) -> SetupData:
    """Parse the client's setup message into glossary + optional voice replication data."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return SetupData()
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

    vr_sample = None
    vr_consent = None
    vr = data.get("voiceReplication")
    if isinstance(vr, dict):
        sample_b64 = vr.get("voiceSample")
        consent_b64 = vr.get("consentAudio")
        if isinstance(sample_b64, str) and isinstance(consent_b64, str):
            try:
                vr_sample = base64.b64decode(sample_b64)
                vr_consent = base64.b64decode(consent_b64)
                logger.info(
                    "Voice replication: sample=%d bytes, consent=%d bytes",
                    len(vr_sample), len(vr_consent),
                )
            except Exception:  # noqa: BLE001
                logger.warning("Invalid base64 in voiceReplication data")

    return SetupData(glossary=entries, vr_voice_sample=vr_sample, vr_consent_audio=vr_consent)


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
    setup_data: SetupData | None = None
    try:
        setup_raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=SETUP_TIMEOUT_SEC
        )
        setup_data = _parse_setup(setup_raw)
        logger.debug("Setup received: %d glossary entries", len(setup_data.glossary))
    except asyncio.TimeoutError:
        logger.warning(
            "No setup message within %ds; using default glossary.", SETUP_TIMEOUT_SEC
        )
    except WebSocketDisconnect:
        logger.debug("Client disconnected before sending setup")
        return

    glossary_entries = setup_data.glossary if setup_data else None
    vr_voice_sample = setup_data.vr_voice_sample if setup_data else None
    vr_consent_audio = setup_data.vr_consent_audio if setup_data else None
    vr_enabled = vr_voice_sample is not None and vr_consent_audio is not None

    system_instruction = build_system_instruction(source, target, glossary_entries)
    display_map = _build_display_map(
        glossary_entries if glossary_entries is not None else load_default_glossary()
    )
    active_model = VR_MODEL if vr_enabled else MODEL
    if vr_enabled:
        logger.info("Voice replication enabled, using model %s", active_model)

    # Shared state between the upstream forwarder and the session loop. The
    # forwarder has the lifetime of the browser WebSocket and writes to whichever
    # Live session is currently open; the session loop tears down old sessions
    # and opens fresh ones (with the resumption handle) as the Live API expires
    # them, without ever closing the browser-facing WS.
    current_session: types.AsyncSession | None = None

    async def upstream_task() -> None:
        """Forward browser audio into whichever Live session is current."""
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    logger.debug("Upstream: client disconnected")
                    return
                if "bytes" in message:
                    audio = message["bytes"]
                    sess = current_session
                    if sess is None:
                        continue
                    try:
                        await sess.send_realtime_input(
                            audio=types.Blob(
                                mime_type="audio/pcm;rate=16000", data=audio
                            )
                        )
                    except Exception:  # noqa: BLE001
                        pass
                elif "text" in message:
                    logger.debug("Ignoring text message (audio-only)")
        except WebSocketDisconnect:
            logger.debug("Upstream: client disconnected")

    async def session_loop() -> None:
        """Open Gemini Live sessions in succession, replacing on GoAway."""
        nonlocal current_session

        def _build_config():
            cfg = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
            )
            if vr_enabled:
                cfg.speech_config = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        replicated_voice_config=types.ReplicatedVoiceConfig(
                            mime_type="audio/wav",
                            voice_sample_audio=vr_voice_sample,
                            consent_audio=vr_consent_audio,
                        )
                    )
                )
            return cfg

        next_ready = asyncio.Event()
        next_session_ref: list = [None]
        next_conn_ref: list = [None]
        is_first_session = True

        async def _open_next():
            """Open and store the next session (runs concurrently with drain)."""
            try:
                cfg = _build_config()
                conn = client.aio.live.connect(model=active_model, config=cfg)
                sess = await conn.__aenter__()
                next_session_ref[0] = sess
                next_conn_ref[0] = conn
                logger.debug("Next session ready")
            except Exception:  # noqa: BLE001
                logger.warning("Failed to open next session", exc_info=True)
            next_ready.set()

        def _cleanup_next():
            if next_conn_ref[0] is not None:
                conn = next_conn_ref[0]
                next_conn_ref[0] = None
                next_session_ref[0] = None
                next_ready.clear()
                asyncio.create_task(conn.__aexit__(None, None, None))

        retry_backoff = RETRY_BACKOFF_INIT
        while True:
            conn = None
            open_next_task = None
            error_cleanup = False
            try:
                if not is_first_session:
                    await next_ready.wait()

                if next_session_ref[0] is not None:
                    session = next_session_ref[0]
                    conn = next_conn_ref[0]
                    next_session_ref[0] = None
                    next_conn_ref[0] = None
                    next_ready.clear()
                    logger.debug("Using pre-opened session")
                else:
                    cfg = _build_config()
                    conn = client.aio.live.connect(model=active_model, config=cfg)
                    session = await asyncio.wait_for(
                        conn.__aenter__(), timeout=CONNECT_TIMEOUT_SEC
                    )
                    logger.debug("Opened fresh Live session")

                is_first_session = False
                current_session = session
                retry_backoff = RETRY_BACKOFF_INIT

                go_away_event = asyncio.Event()
                go_away_secs: float = 30

                async def _relay_session() -> None:
                    """Forward Gemini messages to the browser until session ends."""
                    while True:
                        saw_message = False
                        async for msg in session.receive():
                            saw_message = True
                            if msg.go_away is not None:
                                tl = msg.go_away.time_left or "30s"
                                nonlocal go_away_secs
                                go_away_secs = (
                                    int(tl.rstrip("s"))
                                    if tl.endswith("s")
                                    else 30
                                )
                                logger.info(
                                    "GoAway received (time_left=%s); "
                                    "opening next session",
                                    msg.go_away.time_left,
                                )
                                go_away_event.set()
                                continue
                            envelope = _envelope_from(msg)
                            if envelope is None:
                                continue
                            ot = envelope.get("outputTranscription")
                            if ot and ot.get("text") and display_map:
                                original = ot["text"]
                                replaced = _apply_display_map(
                                    original, display_map
                                )
                                if replaced != original:
                                    logger.debug(
                                        "Display map: %r -> %r",
                                        original,
                                        replaced,
                                    )
                                ot["text"] = replaced
                            await websocket.send_text(
                                json.dumps(envelope)
                            )
                            if go_away_event.is_set():
                                sc = msg.server_content
                                if sc and sc.turn_complete:
                                    logger.debug(
                                        "Turn complete after GoAway; "
                                        "reopening"
                                    )
                                    return
                        if not saw_message:
                            return

                relay_task = asyncio.create_task(_relay_session())
                go_away_wait = asyncio.create_task(go_away_event.wait())
                done, _ = await asyncio.wait(
                    {relay_task, go_away_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if go_away_wait in done and relay_task not in done:
                    open_next_task = asyncio.create_task(_open_next())
                    try:
                        await asyncio.wait_for(
                            relay_task, timeout=go_away_secs
                        )
                    except asyncio.TimeoutError:
                        logger.debug(
                            "GoAway deadline reached; reopening"
                        )
                        relay_task.cancel()
                        try:
                            await relay_task
                        except asyncio.CancelledError:
                            pass
                else:
                    go_away_wait.cancel()
                    if relay_task.done() and relay_task.exception():
                        raise relay_task.exception()
                logger.debug("Live session ended; reopening")
            except WebSocketDisconnect:
                error_cleanup = True
                raise
            except Exception:  # noqa: BLE001
                error_cleanup = True
                logger.warning(
                    "Session error; retrying in %.1fs", retry_backoff,
                    exc_info=True,
                )
                await asyncio.sleep(retry_backoff)
                retry_backoff = min(retry_backoff * 2, RETRY_BACKOFF_MAX)
            finally:
                current_session = None
                if conn is not None:
                    try:
                        await conn.__aexit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass
                if error_cleanup:
                    if open_next_task and not open_next_task.done():
                        open_next_task.cancel()
                    _cleanup_next()

    try:
        up = asyncio.create_task(upstream_task())
        loop_task = asyncio.create_task(session_loop())
        done, pending = await asyncio.wait(
            {up, loop_task}, return_when=asyncio.FIRST_COMPLETED
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
