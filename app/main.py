"""FastAPI application for real-time live translation using the Gemini Live API."""

import asyncio
import base64
import json
import logging
import os
import sys
import time
import unicodedata
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
_WARMUP_PCM = (Path(__file__).parent / "warmup.pcm").read_bytes()
SETUP_TIMEOUT_SEC = 5  # how long to wait for the client's setup message
AUTHOR = "live_translator"  # constant author tag echoed in every server frame
RESUME_TTL_SEC = 900  # local eviction buffer; Live API has the real ~10min window

# session_id -> (resumption_handle, monotonic_timestamp). Lazily evicted on
# access. Single-process, single-replica only — multi-replica needs a shared
# store (Redis), but README pins --max-instances 1 so this is sufficient.
_resume_handles: dict[str, tuple[str, float]] = {}


def _resume_handle_get(session_id: str) -> str | None:
    """Look up the most recent resumption handle for `session_id`, evicting stale entries."""
    now = time.monotonic()
    for k, (_, ts) in list(_resume_handles.items()):
        if now - ts > RESUME_TTL_SEC:
            _resume_handles.pop(k, None)
    entry = _resume_handles.get(session_id)
    return entry[0] if entry else None


def _resume_handle_put(session_id: str, handle: str) -> None:
    _resume_handles[session_id] = (handle, time.monotonic())


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
    display_map = _build_display_map(
        glossary_entries if glossary_entries is not None else load_default_glossary()
    )

    async def _warmup_session(session) -> None:
        """Send a short speech clip to force a full model turn, then discard the output.

        This primes the model so it responds immediately to real audio, and
        on resumed sessions it flushes the replayed previous-turn translation
        that session resumption sometimes re-emits.
        """
        t0 = time.monotonic()
        model_responded = False
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            logger.debug("Warm-up attempt %d/%d", attempt, max_attempts)
            await session.send_realtime_input(
                audio=types.Blob(
                    mime_type="audio/pcm;rate=16000", data=_WARMUP_PCM
                )
            )
            await asyncio.sleep(0.1)
            await session.send_realtime_input(
                audio=types.Blob(
                    mime_type="audio/pcm;rate=16000",
                    data=b"\x00" * 48000,
                )
            )
            try:
                async with asyncio.timeout(3):
                    async for msg in session.receive():
                        update = msg.session_resumption_update
                        if update and update.resumable and update.new_handle:
                            _resume_handle_put(session_id, update.new_handle)
                        sc = msg.server_content
                        if sc:
                            if (
                                not model_responded
                                and sc.model_turn
                                and sc.model_turn.parts
                            ):
                                for p in sc.model_turn.parts:
                                    if (
                                        p.inline_data
                                        and p.inline_data.data
                                        and len(p.inline_data.data) > 0
                                    ):
                                        model_responded = True
                                        logger.debug(
                                            "Warm-up: model responded in %.1fs"
                                            " (attempt %d)",
                                            time.monotonic() - t0,
                                            attempt,
                                        )
                                        await websocket.send_text(
                                            json.dumps(
                                                {
                                                    "author": AUTHOR,
                                                    "ready": True,
                                                }
                                            )
                                        )
                                        break
                            if sc.turn_complete:
                                logger.debug(
                                    "Warm-up turn complete in %.1fs",
                                    time.monotonic() - t0,
                                )
                                if not model_responded:
                                    continue
                                # Drain late transcriptions that the Live API
                                # sends asynchronously after turn_complete.
                                try:
                                    async with asyncio.timeout(1.0):
                                        async for late in session.receive():
                                            upd = late.session_resumption_update
                                            if upd and upd.resumable and upd.new_handle:
                                                _resume_handle_put(session_id, upd.new_handle)
                                except TimeoutError:
                                    pass
                                return
            except TimeoutError:
                logger.debug(
                    "Warm-up attempt %d timed out after 3s", attempt
                )
                continue

            if model_responded:
                return

        logger.debug(
            "Warm-up: no response after %d attempts (%.1fs); proceeding",
            max_attempts,
            time.monotonic() - t0,
        )
        if not model_responded:
            await websocket.send_text(
                json.dumps({"author": AUTHOR, "ready": True})
            )

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
                        # No active session right now (between upstream
                        # reconnects). Drop the chunk; resumption preserves
                        # model context across the gap.
                        continue
                    try:
                        await sess.send_realtime_input(
                            audio=types.Blob(
                                mime_type="audio/pcm;rate=16000", data=audio
                            )
                        )
                    except Exception:  # noqa: BLE001
                        # Session closed mid-send; drop and let the loop reopen.
                        pass
                elif "text" in message:
                    logger.debug("Ignoring text message (audio-only)")
        except WebSocketDisconnect:
            logger.debug("Upstream: client disconnected")

    async def session_loop() -> None:
        """Open Gemini Live sessions in succession, resuming each via stored handle."""
        nonlocal current_session
        while True:
            prior_handle = _resume_handle_get(session_id)
            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                session_resumption=types.SessionResumptionConfig(handle=prior_handle),
            )
            logger.debug(
                "Opening Live session (resume=%s)", "yes" if prior_handle else "no"
            )
            try:
                async with client.aio.live.connect(model=MODEL, config=config) as session:
                    await _warmup_session(session)
                    current_session = session
                    try:
                        go_away_event = asyncio.Event()
                        go_away_secs: float = 30

                        async def _drain_session() -> None:
                            """Read from the Live session until it ends or GoAway deadline."""
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
                                            "draining for up to %ds",
                                            msg.go_away.time_left,
                                            go_away_secs,
                                        )
                                        go_away_event.set()
                                        continue
                                    update = msg.session_resumption_update
                                    if (
                                        update
                                        and update.resumable
                                        and update.new_handle
                                    ):
                                        _resume_handle_put(
                                            session_id, update.new_handle
                                        )
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

                        drain_task = asyncio.create_task(_drain_session())
                        go_away_wait = asyncio.create_task(go_away_event.wait())
                        done, _ = await asyncio.wait(
                            {drain_task, go_away_wait},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if go_away_wait in done and drain_task not in done:
                            try:
                                await asyncio.wait_for(
                                    drain_task, timeout=go_away_secs
                                )
                            except asyncio.TimeoutError:
                                logger.debug(
                                    "GoAway deadline reached; reopening"
                                )
                                drain_task.cancel()
                                try:
                                    await drain_task
                                except asyncio.CancelledError:
                                    pass
                        else:
                            go_away_wait.cancel()
                            if drain_task.done() and drain_task.exception():
                                raise drain_task.exception()
                        logger.debug(
                            "Live session ended; reopening with stored handle"
                        )
                    finally:
                        current_session = None
            except WebSocketDisconnect:
                raise
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Upstream session error; reopening in 1s", exc_info=True
                )
                current_session = None
                await asyncio.sleep(1)

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
