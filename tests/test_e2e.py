"""E2E test: connect via WebSocket, send audio, verify transcription."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import websockets

SERVER_URL = "ws://localhost:8001/ws/test-user"
CHUNK_SIZE = 512  # bytes per frame (256 samples * 2 bytes)
CHUNK_INTERVAL = 0.016  # ~16ms per chunk at 16kHz
WAIT_FOR_RESPONSE = 15  # seconds to wait after sending audio

# macOS say voice for each source language
SAY_VOICES = {
    "en": "Samantha",
    "ja": "Kyoko",
    "zh": "Ting-Ting",
    "es": "Monica",
    "fr": "Thomas",
    "de": "Anna",
    "ko": "Yuna",
    "pt": "Luciana",
}

# Test cases: (source, target, input_text, description)
TEST_CASES = [
    ("en", "ja", "Hello, this is a test of the live translation system.", "English to Japanese"),
    ("en", "es", "The weather is beautiful today.", "English to Spanish"),
    ("en", "fr", "Thank you very much for your help.", "English to French"),
    ("en", "ko", "Nice to meet you.", "English to Korean"),
    ("en", "zh", "Good morning, how are you?", "English to Chinese"),
]


def generate_test_audio(text: str, voice: str = "Samantha") -> bytes:
    """Generate PCM 16kHz mono audio from text using macOS say + ffmpeg.

    Adds 1s silence before and after speech to help VAD detect boundaries.
    """
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as aiff_f:
        aiff_path = aiff_f.name
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as pcm_f:
        pcm_path = pcm_f.name

    try:
        subprocess.run(
            ["say", "-v", voice, "-o", aiff_path, text],
            check=True,
            capture_output=True,
        )
        # Add 1s silence padding before and after speech for VAD
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-i", aiff_path,
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-filter_complex",
                "[0]atrim=0:1[pre];[1]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono[speech];[2]atrim=0:1[post];[pre][speech][post]concat=n=3:v=0:a=1[out]",
                "-map", "[out]",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                pcm_path,
            ],
            check=True,
            capture_output=True,
        )
        return Path(pcm_path).read_bytes()
    finally:
        Path(aiff_path).unlink(missing_ok=True)
        Path(pcm_path).unlink(missing_ok=True)


async def run_single_test(
    source: str, target: str, text: str, description: str, session_id: str
) -> dict:
    """Run a single translation test. Returns result dict."""
    voice = SAY_VOICES.get(source, "Samantha")
    print(f"\n{'─' * 60}")
    print(f"TEST: {description} ({source} → {target})")
    print(f"Input text: {text}")
    print(f"Voice: {voice}")
    print(f"{'─' * 60}")

    pcm_data = generate_test_audio(text, voice)
    print(f"Audio: {len(pcm_data)} bytes ({len(pcm_data) / 32000:.1f}s)")

    url = f"{SERVER_URL}/{session_id}?source={source}&target={target}"

    input_transcriptions = []
    output_transcriptions = []
    audio_chunks_received = 0
    events_received = 0
    turn_complete = asyncio.Event()

    async with websockets.connect(url) as ws:
        await asyncio.sleep(5)
        print("Sending audio...")

        async def send_audio():
            offset = 0
            chunks_sent = 0
            while offset < len(pcm_data):
                chunk = pcm_data[offset : offset + CHUNK_SIZE]
                await ws.send(chunk)
                chunks_sent += 1
                offset += CHUNK_SIZE
                await asyncio.sleep(CHUNK_INTERVAL)
            print(f"Sent {chunks_sent} chunks. Sending trailing silence...")
            silence = b"\x00" * CHUNK_SIZE
            for _ in range(int(5.0 / CHUNK_INTERVAL)):
                await ws.send(silence)
                await asyncio.sleep(CHUNK_INTERVAL)

        async def receive_events():
            nonlocal audio_chunks_received, events_received
            try:
                async for message in ws:
                    event = json.loads(message)
                    events_received += 1

                    if event.get("inputTranscription"):
                        t = event["inputTranscription"].get("text", "")
                        finished = event["inputTranscription"].get("finished", False)
                        if t:
                            input_transcriptions.append({"text": t, "finished": finished})
                            status = "FINAL" if finished else "partial"
                            print(f"  [INPUT {status}] {t}")

                    if event.get("outputTranscription"):
                        t = event["outputTranscription"].get("text", "")
                        finished = event["outputTranscription"].get("finished", False)
                        if t:
                            output_transcriptions.append({"text": t, "finished": finished})
                            status = "FINAL" if finished else "partial"
                            print(f"  [OUTPUT {status}] {t}")

                    if event.get("content", {}).get("parts"):
                        for part in event["content"]["parts"]:
                            if part.get("inlineData"):
                                audio_chunks_received += 1

                    if event.get("turnComplete"):
                        print("  [TURN COMPLETE]")
                        turn_complete.set()

            except websockets.exceptions.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(receive_events())
        send_task = asyncio.create_task(send_audio())

        await send_task
        try:
            await asyncio.wait_for(turn_complete.wait(), timeout=WAIT_FOR_RESPONSE)
        except asyncio.TimeoutError:
            print(f"Timeout after {WAIT_FOR_RESPONSE}s")

        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

    # Determine pass/fail
    final_input = [t for t in input_transcriptions if t["finished"]]
    final_output = [t for t in output_transcriptions if t["finished"]]
    input_text = final_input[-1]["text"] if final_input else (
        input_transcriptions[-1]["text"] if input_transcriptions else None
    )
    output_text = final_output[-1]["text"] if final_output else (
        output_transcriptions[-1]["text"] if output_transcriptions else None
    )

    passed = bool(input_transcriptions and output_transcriptions and audio_chunks_received)

    result = {
        "description": description,
        "source": source,
        "target": target,
        "input_text": text,
        "heard": input_text,
        "translated": output_text,
        "events": events_received,
        "audio_chunks": audio_chunks_received,
        "passed": passed,
    }

    status = "PASS" if passed else "FAIL"
    print(f"\n  Result: {status}")
    if input_text:
        print(f"  Heard:      {input_text}")
    if output_text:
        print(f"  Translated: {output_text}")

    return result


async def run_all_tests(test_cases: list[tuple]):
    """Run all test cases sequentially and print summary."""
    results = []
    for i, (source, target, text, description) in enumerate(test_cases):
        session_id = f"test-e2e-{i}-{source}-{target}"
        result = await run_single_test(source, target, text, description, session_id)
        results.append(result)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Test':<25} {'Pair':<8} {'Status':<6} {'Translation'}")
    print("-" * 60)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        pair = f"{r['source']}→{r['target']}"
        translation = r["translated"] or "(none)"
        if len(translation) > 40:
            translation = translation[:37] + "..."
        print(f"{r['description']:<25} {pair:<8} {status:<6} {translation}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n{passed}/{total} tests passed")
    return all(r["passed"] for r in results)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        # Single test: python test_e2e.py en ja
        source, target = sys.argv[1], sys.argv[2]
        text = sys.argv[3] if len(sys.argv) > 3 else "Hello, this is a test."
        result = asyncio.run(
            run_single_test(source, target, text, f"{source}→{target}", f"test-e2e-{source}-{target}")
        )
        sys.exit(0 if result["passed"] else 1)
    else:
        # Run all test cases
        result = asyncio.run(run_all_tests(TEST_CASES))
        sys.exit(0 if result else 1)
