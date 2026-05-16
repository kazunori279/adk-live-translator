"""Reproduce context leaking across session resumption.

Opens a Gemini Live session with the translator system instruction,
sends a TTS sentence, captures the output transcription, then resumes
with a new session and sends a *different* sentence. If the output
transcription for the second sentence contains text from the first
translation, that's a context leak.

Loops until a leak is observed or --max-rounds is reached.

Usage:
    uv run python tests/repro_leak.py
    uv run python tests/repro_leak.py --max-rounds 50
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "app" / ".env")
os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
os.environ.pop("GOOGLE_CLOUD_LOCATION", None)

from google import genai
from google.genai import types
from google.cloud import texttospeech

MODEL = "gemini-3.1-flash-live-preview"
SYSTEM = (
    "You are a real-time translator from English to Japanese. "
    "Listen to the incoming audio and immediately output the translated "
    "version in Japanese, maintaining the speaker's original tone and urgency. "
    "Translate only the current utterance. Do not repeat, reference, or "
    "prepend translations from previous turns. Each spoken segment should "
    "produce exactly one translation of that segment and nothing else."
)

SENTENCES = [
    "Hello, good morning.",
    "Thank you very much.",
    "Where is the station?",
    "I like sushi.",
    "See you tomorrow.",
]


def synthesize_pcm(text: str) -> bytes:
    """Convert text to 16kHz mono PCM via Google Cloud TTS."""
    tts = texttospeech.TextToSpeechClient()
    resp = tts.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code="en-US",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        ),
    )
    # Strip 44-byte WAV header
    return resp.audio_content[44:]


async def run_turn(
    client: genai.Client,
    audio: bytes,
    handle: str | None,
    warmup_audio: bytes | None = None,
) -> tuple[str, str | None]:
    """Send audio through a Live session, return (output_transcription, new_handle)."""
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM)]
        ),
        session_resumption=types.SessionResumptionConfig(handle=handle),
    )

    output_parts: list[str] = []
    new_handle: str | None = None

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        # Warmup: after resumption, send real speech to force a full turn, then discard
        if handle and warmup_audio:
            await session.send_realtime_input(
                audio=types.Blob(mime_type="audio/pcm;rate=16000", data=warmup_audio)
            )
            await asyncio.sleep(0.1)
            await session.send_realtime_input(
                audio=types.Blob(mime_type="audio/pcm;rate=16000", data=b"\x00" * 48000)
            )
            try:
                async with asyncio.timeout(10):
                    async for msg in session.receive():
                        update = msg.session_resumption_update
                        if update and update.resumable and update.new_handle:
                            new_handle = update.new_handle
                        sc = msg.server_content
                        if sc and sc.turn_complete:
                            print("      (warmup turn complete, flushed)")
                            break
            except TimeoutError:
                print("      (warmup timeout)")

        # Send all audio at once (model buffers it), then silence for VAD
        await session.send_realtime_input(
            audio=types.Blob(mime_type="audio/pcm;rate=16000", data=audio)
        )
        await asyncio.sleep(0.1)
        # 1.5s silence to trigger end-of-speech
        await session.send_realtime_input(
            audio=types.Blob(mime_type="audio/pcm;rate=16000", data=b"\x00" * 48000)
        )

        # Drain until turnComplete
        try:
            async with asyncio.timeout(30):
                async for msg in session.receive():
                    update = msg.session_resumption_update
                    if update and update.resumable and update.new_handle:
                        new_handle = update.new_handle

                    sc = msg.server_content
                    if sc:
                        if sc.output_transcription and sc.output_transcription.text:
                            output_parts.append(sc.output_transcription.text)
                        if sc.turn_complete:
                            break
        except TimeoutError:
            print("      (timeout waiting for turnComplete)")

    return "".join(output_parts), new_handle


async def main():
    parser = argparse.ArgumentParser(description="Reproduce context leak")
    parser.add_argument("--max-rounds", type=int, default=30)
    args = parser.parse_args()

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    # Pre-synthesize all audio
    print("Synthesizing TTS audio...")
    audio_cache: dict[str, bytes] = {}
    for s in SENTENCES:
        audio_cache[s] = synthesize_pcm(s)
    warmup_audio = synthesize_pcm("Hello.")
    print(f"  {len(SENTENCES)} sentences + warmup ready\n")

    handle: str | None = None
    prev_sentence: str | None = None
    prev_output: str | None = None
    leaks_found = 0

    for i in range(args.max_rounds):
        # Alternate sentences so consecutive turns differ
        sentence = SENTENCES[i % len(SENTENCES)]
        audio = audio_cache[sentence]

        t0 = time.monotonic()
        output, new_handle = await run_turn(client, audio, handle, warmup_audio)
        elapsed = time.monotonic() - t0

        is_resumed = handle is not None
        status = "RESUMED" if is_resumed else "FRESH"

        print(f"[{i+1:3d}] {status} ({elapsed:.1f}s)")
        print(f"      IN:  {sentence}")
        print(f"      OUT: {output}")

        # Check for leak: does the output contain significant text from previous output?
        if prev_output and is_resumed and prev_output:
            # Check if a substantial chunk of the previous output appears in current output
            prev_clean = prev_output.strip()
            out_clean = output.strip()
            if len(prev_clean) > 5 and prev_clean in out_clean and len(out_clean) > len(prev_clean) * 1.3:
                leaks_found += 1
                print(f"      *** LEAK DETECTED *** (prev output embedded in current)")
                print(f"      PREV: {prev_output}")
            elif len(prev_clean) > 10:
                # Check if first half of prev output appears at start of current
                half = prev_clean[: len(prev_clean) // 2]
                if len(half) > 5 and out_clean.startswith(half):
                    leaks_found += 1
                    print(f"      *** LEAK DETECTED *** (current starts with prev output)")
                    print(f"      PREV: {prev_output}")

        print()
        handle = new_handle
        prev_sentence = sentence
        prev_output = output

    print(f"=== Done: {args.max_rounds} rounds, {leaks_found} leaks detected ===")


if __name__ == "__main__":
    asyncio.run(main())
