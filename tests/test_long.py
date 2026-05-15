"""Long-running soak test for the live translator.

Generates random English sentences, converts to audio via Cloud TTS,
sends through the translator WebSocket, transcribes the response via
Cloud STT, and verifies semantic correctness with Gemini Flash Lite.

Runs on a single persistent WebSocket to exercise session resumption,
GoAway handling, and translation quality over extended periods.

Usage:
    uv run python test_long.py [--url ws://localhost:8000] [--duration 3600] [--source en] [--target ja]
"""

import argparse
import asyncio
import base64
import json
import os
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import certifi
import websockets
from dotenv import load_dotenv
from google import genai
from google.cloud import speech, texttospeech

load_dotenv(Path(__file__).parent.parent / "app" / ".env")

os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
os.environ.pop("GOOGLE_CLOUD_LOCATION", None)

CHUNK_SIZE = 512
CHUNK_INTERVAL = 0.016
RESPONSE_TIMEOUT = 30
SILENCE_AFTER_SPEECH = 2.0
GENAI_MODEL = "gemini-2.5-flash-lite"

TOPICS = [
    "technology and software engineering",
    "travel and geography",
    "food and cooking",
    "business and finance",
    "science and nature",
    "sports and fitness",
    "art and music",
    "history and culture",
    "health and medicine",
    "education and learning",
    "weather and seasons",
    "daily life and routines",
    "news and current events",
    "philosophy and ethics",
]


LATENCY_THRESHOLD = 5.0  # seconds — first response must arrive within this

TEST_GLOSSARY: list[dict[str, str]] = [
    {"source": "Kubernetes", "target": "クバネティス", "transcription": "Kubernetes"},
    {"source": "Cloud Run", "target": "クラウドラン", "transcription": "Cloud Run"},
    {"source": "Gemini", "target": "ジェミニ", "transcription": "Gemini"},
    {"source": "Vertex AI", "target": "バーテックスエーアイ", "transcription": "Vertex AI"},
    {"source": "TensorFlow", "target": "テンソルフロー", "transcription": "TensorFlow"},
    {"source": "BigQuery", "target": "ビッグクエリ", "transcription": "BigQuery"},
    {"source": "Firestore", "target": "ファイアストア", "transcription": "Firestore"},
    {"source": "Cloud Spanner", "target": "クラウドスパナー", "transcription": "Cloud Spanner"},
    {"source": "Pub/Sub", "target": "パブサブ", "transcription": "Pub/Sub"},
    {"source": "Dataflow", "target": "データフロー", "transcription": "Dataflow"},
    {"source": "Anthos", "target": "アンソス", "transcription": "Anthos"},
    {"source": "Istio", "target": "イスティオ", "transcription": "Istio"},
    {"source": "gRPC", "target": "ジーアールピーシー", "transcription": "gRPC"},
    {"source": "Protocol Buffers", "target": "プロトコルバッファーズ", "transcription": "Protocol Buffers"},
    {"source": "Docker", "target": "ドッカー", "transcription": "Docker"},
    {"source": "Terraform", "target": "テラフォーム", "transcription": "Terraform"},
    {"source": "Jenkins", "target": "ジェンキンズ", "transcription": "Jenkins"},
    {"source": "GitHub Actions", "target": "ギットハブアクションズ", "transcription": "GitHub Actions"},
    {"source": "Visual Studio Code", "target": "ビジュアルスタジオコード", "transcription": "VS Code"},
    {"source": "IntelliJ", "target": "インテリジェイ", "transcription": "IntelliJ"},
    {"source": "PostgreSQL", "target": "ポストグレスキューエル", "transcription": "PostgreSQL"},
    {"source": "MongoDB", "target": "モンゴディービー", "transcription": "MongoDB"},
    {"source": "Redis", "target": "レディス", "transcription": "Redis"},
    {"source": "Elasticsearch", "target": "エラスティックサーチ", "transcription": "Elasticsearch"},
    {"source": "Kafka", "target": "カフカ", "transcription": "Kafka"},
    {"source": "RabbitMQ", "target": "ラビットエムキュー", "transcription": "RabbitMQ"},
    {"source": "GraphQL", "target": "グラフキューエル", "transcription": "GraphQL"},
    {"source": "REST API", "target": "レストエーピーアイ", "transcription": "REST API"},
    {"source": "WebSocket", "target": "ウェブソケット", "transcription": "WebSocket"},
    {"source": "OAuth", "target": "オーオース", "transcription": "OAuth"},
    {"source": "JWT", "target": "ジェイダブリューティー", "transcription": "JWT"},
    {"source": "SSL", "target": "エスエスエル", "transcription": "SSL"},
    {"source": "DNS", "target": "ディーエヌエス", "transcription": "DNS"},
    {"source": "CDN", "target": "シーディーエヌ", "transcription": "CDN"},
    {"source": "load balancer", "target": "ロードバランサー", "transcription": "ロードバランサー"},
    {"source": "microservices", "target": "マイクロサービス", "transcription": "マイクロサービス"},
    {"source": "serverless", "target": "サーバーレス", "transcription": "サーバーレス"},
    {"source": "CI/CD", "target": "シーアイシーディー", "transcription": "CI/CD"},
    {"source": "DevOps", "target": "デブオプス", "transcription": "DevOps"},
    {"source": "SRE", "target": "エスアールイー", "transcription": "SRE"},
    {"source": "Agile", "target": "アジャイル", "transcription": "Agile"},
    {"source": "Scrum", "target": "スクラム", "transcription": "Scrum"},
    {"source": "Kanban", "target": "カンバン", "transcription": "Kanban"},
    {"source": "sprint", "target": "スプリント", "transcription": "スプリント"},
    {"source": "backlog", "target": "バックログ", "transcription": "バックログ"},
    {"source": "React", "target": "リアクト", "transcription": "React"},
    {"source": "Angular", "target": "アンギュラー", "transcription": "Angular"},
    {"source": "Vue.js", "target": "ビュージェイエス", "transcription": "Vue.js"},
    {"source": "Next.js", "target": "ネクストジェイエス", "transcription": "Next.js"},
    {"source": "Node.js", "target": "ノードジェイエス", "transcription": "Node.js"},
    {"source": "TypeScript", "target": "タイプスクリプト", "transcription": "TypeScript"},
    {"source": "Python", "target": "パイソン", "transcription": "Python"},
    {"source": "Golang", "target": "ゴーラング", "transcription": "Go"},
    {"source": "Rust", "target": "ラスト", "transcription": "Rust"},
    {"source": "Swift", "target": "スウィフト", "transcription": "Swift"},
    {"source": "Kotlin", "target": "コトリン", "transcription": "Kotlin"},
    {"source": "Flutter", "target": "フラッター", "transcription": "Flutter"},
    {"source": "Dart", "target": "ダート", "transcription": "Dart"},
    {"source": "machine learning", "target": "マシンラーニング", "transcription": "機械学習"},
    {"source": "deep learning", "target": "ディープラーニング", "transcription": "深層学習"},
    {"source": "neural network", "target": "ニューラルネットワーク", "transcription": "ニューラルネットワーク"},
    {"source": "transformer", "target": "トランスフォーマー", "transcription": "Transformer"},
    {"source": "fine-tuning", "target": "ファインチューニング", "transcription": "ファインチューニング"},
    {"source": "embedding", "target": "エンベディング", "transcription": "エンベディング"},
    {"source": "RAG", "target": "ラグ", "transcription": "RAG"},
    {"source": "LLM", "target": "エルエルエム", "transcription": "LLM"},
    {"source": "GPT", "target": "ジーピーティー", "transcription": "GPT"},
    {"source": "Claude", "target": "クロード", "transcription": "Claude"},
    {"source": "ChatGPT", "target": "チャットジーピーティー", "transcription": "ChatGPT"},
    {"source": "Hugging Face", "target": "ハギングフェイス", "transcription": "Hugging Face"},
    {"source": "PyTorch", "target": "パイトーチ", "transcription": "PyTorch"},
    {"source": "JAX", "target": "ジャックス", "transcription": "JAX"},
    {"source": "CUDA", "target": "クーダ", "transcription": "CUDA"},
    {"source": "TPU", "target": "ティーピーユー", "transcription": "TPU"},
    {"source": "GPU", "target": "ジーピーユー", "transcription": "GPU"},
    {"source": "API gateway", "target": "エーピーアイゲートウェイ", "transcription": "APIゲートウェイ"},
    {"source": "service mesh", "target": "サービスメッシュ", "transcription": "サービスメッシュ"},
    {"source": "observability", "target": "オブザーバビリティ", "transcription": "オブザーバビリティ"},
    {"source": "Prometheus", "target": "プロメテウス", "transcription": "Prometheus"},
    {"source": "Grafana", "target": "グラファナ", "transcription": "Grafana"},
    {"source": "OpenTelemetry", "target": "オープンテレメトリー", "transcription": "OpenTelemetry"},
    {"source": "Helm", "target": "ヘルム", "transcription": "Helm"},
    {"source": "Argo CD", "target": "アルゴシーディー", "transcription": "Argo CD"},
    {"source": "Flux", "target": "フラックス", "transcription": "Flux"},
    {"source": "GitOps", "target": "ギットオプス", "transcription": "GitOps"},
    {"source": "infrastructure as code", "target": "インフラストラクチャーアズコード", "transcription": "IaC"},
    {"source": "Pulumi", "target": "プルミ", "transcription": "Pulumi"},
    {"source": "Ansible", "target": "アンシブル", "transcription": "Ansible"},
    {"source": "Vault", "target": "ボルト", "transcription": "Vault"},
    {"source": "Consul", "target": "コンサル", "transcription": "Consul"},
    {"source": "Envoy", "target": "エンボイ", "transcription": "Envoy"},
    {"source": "Nginx", "target": "エンジンエックス", "transcription": "Nginx"},
    {"source": "Apache", "target": "アパッチ", "transcription": "Apache"},
    {"source": "Linux", "target": "リナックス", "transcription": "Linux"},
    {"source": "Ubuntu", "target": "ウブントゥ", "transcription": "Ubuntu"},
    {"source": "Debian", "target": "デビアン", "transcription": "Debian"},
    {"source": "Alpine", "target": "アルパイン", "transcription": "Alpine"},
    {"source": "Bazel", "target": "バゼル", "transcription": "Bazel"},
    {"source": "Webpack", "target": "ウェブパック", "transcription": "Webpack"},
    {"source": "Vite", "target": "ヴィート", "transcription": "Vite"},
    {"source": "ESLint", "target": "イーエスリント", "transcription": "ESLint"},
]


@dataclass
class IterationResult:
    index: int
    original: str
    input_transcription: str | None = None
    output_transcription: str | None = None
    stt_transcription: str | None = None
    passed: bool = False
    score: float = 0.0
    reason: str = ""
    error: str | None = None
    elapsed: float = 0.0
    first_response_sec: float | None = None
    turn_complete_sec: float | None = None
    glossary_term: str | None = None
    glossary_found: bool | None = None
    input_transcription_score: float | None = None
    output_transcription_score: float | None = None


@dataclass
class Stats:
    iterations: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total_score: float = 0.0
    latency_ok: int = 0
    latency_slow: int = 0
    glossary_checked: int = 0
    glossary_found: int = 0
    input_transcription_scores: list[float] = field(default_factory=list)
    output_transcription_scores: list[float] = field(default_factory=list)
    results: list[IterationResult] = field(default_factory=list)


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def generate_sentence(client: genai.Client, topic: str, glossary_term: str | None = None) -> str:
    if glossary_term:
        prompt = (
            f"Generate exactly one natural English sentence (10-20 words) that "
            f"uses the term \"{glossary_term}\" naturally. "
            f"Output only the sentence, no quotes or explanation."
        )
    else:
        prompt = (
            f"Generate exactly one natural English sentence (10-20 words) about "
            f"{topic}. Output only the sentence, no quotes or explanation."
        )
    resp = client.models.generate_content(model=GENAI_MODEL, contents=prompt)
    return resp.text.strip().strip('"')


def text_to_pcm(tts_client: texttospeech.TextToSpeechClient, text: str) -> bytes:
    resp = tts_client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Neural2-J",
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        ),
    )
    # Strip the 44-byte WAV header to get raw PCM
    audio = resp.audio_content
    if audio[:4] == b"RIFF":
        audio = audio[44:]
    # Pad 1s silence before and after for VAD
    silence = b"\x00\x00" * 16000
    return silence + audio + silence


def pcm_to_text(
    stt_client: speech.SpeechClient,
    pcm_data: bytes,
    sample_rate: int,
    language: str,
) -> str:
    resp = stt_client.recognize(
        config=speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            language_code=language,
            enable_automatic_punctuation=True,
        ),
        audio=speech.RecognitionAudio(content=pcm_data),
    )
    return " ".join(r.alternatives[0].transcript for r in resp.results if r.alternatives)


def verify_translation(
    client: genai.Client,
    original: str,
    translated: str,
    source: str,
    target: str,
) -> tuple[bool, float, str]:
    resp = client.models.generate_content(
        model=GENAI_MODEL,
        contents=(
            f"You are a translation quality evaluator. Compare the original "
            f"{source} sentence with its {target} translation.\n\n"
            f"Original ({source}): {original}\n"
            f"Translation ({target}): {translated}\n\n"
            f"Score the semantic accuracy from 0 to 10 (10 = perfect). "
            f"Reply in exactly this format:\n"
            f"SCORE: <number>\n"
            f"PASS: <yes/no>\n"
            f"REASON: <one sentence>"
        ),
    )
    text = resp.text.strip()
    score = 0.0
    passed = False
    reason = text
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("SCORE:"):
            try:
                score = float(line.split(":", 1)[1].strip().split("/")[0])
            except ValueError:
                pass
        elif line.upper().startswith("PASS:"):
            passed = "yes" in line.lower()
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return passed, score, reason


def score_transcription(
    client: genai.Client,
    reference: str,
    transcription: str,
    label: str,
) -> float:
    resp = client.models.generate_content(
        model=GENAI_MODEL,
        contents=(
            f"Score how accurately the transcription matches the reference text. "
            f"Ignore minor punctuation or formatting differences. Focus on whether "
            f"the words and meaning are captured correctly.\n\n"
            f"Reference: {reference}\n"
            f"Transcription: {transcription}\n\n"
            f"Score from 0 to 10 (10 = perfect match). "
            f"Reply with ONLY a number, nothing else."
        ),
    )
    try:
        return float(resp.text.strip().split("/")[0])
    except ValueError:
        return 0.0


LANG_TO_STT = {
    "ja": "ja-JP",
    "en": "en-US",
    "zh": "zh-CN",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "ko": "ko-KR",
    "pt": "pt-BR",
    "hi": "hi-IN",
    "ar": "ar-SA",
}


async def run_iteration(
    ws,
    genai_client: genai.Client,
    tts_client: texttospeech.TextToSpeechClient,
    stt_client: speech.SpeechClient,
    index: int,
    source: str,
    target: str,
    glossary_entry: dict[str, str] | None = None,
) -> IterationResult:
    topic = TOPICS[index % len(TOPICS)]
    glossary_term = glossary_entry["source"] if glossary_entry else None
    t0 = time.monotonic()

    try:
        sentence = generate_sentence(genai_client, topic, glossary_term)
    except Exception as e:
        return IterationResult(index=index, original="", error=f"generate: {e}")

    try:
        pcm_data = text_to_pcm(tts_client, sentence)
    except Exception as e:
        return IterationResult(index=index, original=sentence, error=f"tts: {e}")

    input_transcription_final: list[str] = []
    input_transcription_partial: list[str] = []
    output_transcription_final: list[str] = []
    output_transcription_partial: list[str] = []
    audio_chunks: list[bytes] = []
    turn_complete = asyncio.Event()
    first_response_at: list[float] = []
    speech_done_at: list[float] = []
    turn_complete_at: list[float] = []

    async def receive_responses():
        try:
            while not turn_complete.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=RESPONSE_TIMEOUT)
                msg = json.loads(raw)

                has_content = False
                it = msg.get("inputTranscription")
                if it and it.get("text"):
                    if it.get("finished"):
                        input_transcription_final.append(it["text"])
                    else:
                        input_transcription_partial.append(it["text"])

                ot = msg.get("outputTranscription")
                if ot and ot.get("text"):
                    has_content = True
                    if ot.get("finished"):
                        output_transcription_final.append(ot["text"])
                    else:
                        output_transcription_partial.append(ot["text"])

                content = msg.get("content", {})
                for part in content.get("parts", []):
                    inline = part.get("inlineData")
                    if inline and inline.get("data"):
                        has_content = True
                        audio_chunks.append(base64.b64decode(inline["data"]))

                if has_content and not first_response_at:
                    first_response_at.append(time.monotonic())

                if msg.get("turnComplete"):
                    turn_complete_at.append(time.monotonic())
                    turn_complete.set()
        except asyncio.TimeoutError:
            pass
        except websockets.ConnectionClosed:
            pass

    recv_task = asyncio.create_task(receive_responses())

    # Send audio
    offset = 0
    while offset < len(pcm_data):
        chunk = pcm_data[offset : offset + CHUNK_SIZE]
        try:
            await ws.send(chunk)
        except websockets.ConnectionClosed:
            recv_task.cancel()
            return IterationResult(
                index=index, original=sentence, error="ws closed during send"
            )
        offset += CHUNK_SIZE
        await asyncio.sleep(CHUNK_INTERVAL)

    speech_done_at.append(time.monotonic())

    # Trailing silence for VAD
    silence = b"\x00" * CHUNK_SIZE
    for _ in range(int(SILENCE_AFTER_SPEECH / CHUNK_INTERVAL)):
        try:
            await ws.send(silence)
        except websockets.ConnectionClosed:
            break
        await asyncio.sleep(CHUNK_INTERVAL)

    # Wait for response
    try:
        await asyncio.wait_for(turn_complete.wait(), timeout=RESPONSE_TIMEOUT)
    except asyncio.TimeoutError:
        pass

    recv_task.cancel()
    try:
        await recv_task
    except asyncio.CancelledError:
        pass

    # Latency metrics, both measured from end of speech audio (before silence):
    # - first_resp_sec: time to first audio/transcription chunk
    # - turn_complete_sec: time to turnComplete (full translation delivered)
    first_resp_sec = None
    if first_response_at and speech_done_at:
        first_resp_sec = max(0.0, first_response_at[0] - speech_done_at[0])
    turn_comp_sec = None
    if turn_complete_at and speech_done_at:
        turn_comp_sec = max(0.0, turn_complete_at[0] - speech_done_at[0])

    input_text = (
        input_transcription_final[-1]
        if input_transcription_final
        else "".join(input_transcription_partial) or None
    )
    output_text = (
        output_transcription_final[-1]
        if output_transcription_final
        else "".join(output_transcription_partial) or None
    )

    # STT on returned audio
    stt_text = None
    if audio_chunks:
        combined_pcm = b"".join(audio_chunks)
        stt_lang = LANG_TO_STT.get(target, f"{target}-{target.upper()}")
        try:
            stt_text = pcm_to_text(stt_client, combined_pcm, 24000, stt_lang)
        except Exception as e:
            stt_text = f"(stt error: {e})"

    # Transcription quality: input transcription vs known original
    input_tx_score = None
    if input_text:
        try:
            input_tx_score = score_transcription(
                genai_client, sentence, input_text, "input"
            )
        except Exception:
            pass

    # Transcription quality: output transcription vs independent STT
    output_tx_score = None
    if output_text and stt_text and not stt_text.startswith("(stt error"):
        try:
            output_tx_score = score_transcription(
                genai_client, stt_text, output_text, "output"
            )
        except Exception:
            pass

    # Glossary check: verify the display transcription appears in output
    glossary_found = None
    if glossary_entry and output_text:
        expected_display = glossary_entry["transcription"]
        glossary_found = expected_display in output_text

    # Verify translation using the model's own output transcription (not STT),
    # since Cloud STT sometimes mishears correctly-spoken Japanese.
    verify_text = output_text or stt_text or ""
    if not verify_text:
        return IterationResult(
            index=index,
            original=sentence,
            input_transcription=input_text,
            output_transcription=output_text,
            stt_transcription=stt_text,
            error="no response",
            elapsed=time.monotonic() - t0,
            first_response_sec=first_resp_sec,
            turn_complete_sec=turn_comp_sec,
            glossary_term=glossary_term,
            glossary_found=glossary_found,
            input_transcription_score=input_tx_score,
            output_transcription_score=output_tx_score,
        )

    try:
        passed, score, reason = verify_translation(
            genai_client, sentence, verify_text, source, target
        )
    except Exception as e:
        return IterationResult(
            index=index,
            original=sentence,
            input_transcription=input_text,
            output_transcription=output_text,
            stt_transcription=stt_text,
            error=f"verify: {e}",
            elapsed=time.monotonic() - t0,
            first_response_sec=first_resp_sec,
            turn_complete_sec=turn_comp_sec,
            glossary_term=glossary_term,
            glossary_found=glossary_found,
            input_transcription_score=input_tx_score,
            output_transcription_score=output_tx_score,
        )

    return IterationResult(
        index=index,
        original=sentence,
        input_transcription=input_text,
        output_transcription=output_text,
        stt_transcription=stt_text,
        passed=passed,
        score=score,
        reason=reason,
        elapsed=time.monotonic() - t0,
        first_response_sec=first_resp_sec,
        turn_complete_sec=turn_comp_sec,
        glossary_term=glossary_term,
        glossary_found=glossary_found,
        input_transcription_score=input_tx_score,
        output_transcription_score=output_tx_score,
    )


def _format_distribution(
    label: str,
    values: list[float],
    buckets: list[tuple[str, float, float]],
    bar_width: int = 30,
) -> list[str]:
    """Return histogram lines for a list of values.

    `buckets` is a list of (label, low_inclusive, high_exclusive).
    """
    if not values:
        return []
    vals = sorted(values)
    n = len(vals)
    avg = sum(vals) / n
    p50 = vals[n // 2]
    p90 = vals[int(n * 0.9)]
    p99 = vals[int(n * 0.99)]
    lines = [
        f"\n  {label} (n={n})",
        f"  min={vals[0]:.2f}  avg={avg:.2f}  p50={p50:.2f}  p90={p90:.2f}  p99={p99:.2f}  max={vals[-1]:.2f}",
    ]
    counts = []
    for bl, lo, hi in buckets:
        c = sum(1 for v in vals if lo <= v < hi)
        counts.append((bl, c))
    max_c = max(c for _, c in counts) if counts else 1
    for bl, c in counts:
        bar = "#" * int(c / max_c * bar_width) if max_c > 0 else ""
        lines.append(f"  {bl:>10s}: {c:4d} ({100 * c / n:5.1f}%) {bar}")
    return lines


def _format_tags(result: IterationResult) -> tuple[str, str, str, str]:
    display = result.output_transcription or result.stt_transcription or ""
    if len(display) > 40:
        display = display[:37] + "..."

    latency_tag = ""
    tc = result.turn_complete_sec
    if tc is not None:
        if tc > LATENCY_THRESHOLD:
            latency_tag = f" SLOW({tc:.1f}s)"
        else:
            latency_tag = f" {tc:.1f}s"

    glossary_tag = ""
    if result.glossary_term:
        if result.glossary_found:
            glossary_tag = f" [G:{result.glossary_term}:OK]"
        elif result.glossary_found is False:
            glossary_tag = f" [G:{result.glossary_term}:MISS]"
        else:
            glossary_tag = f" [G:{result.glossary_term}:?]"

    tx_tag = ""
    parts = []
    if result.input_transcription_score is not None:
        parts.append(f"in={result.input_transcription_score:.0f}")
    if result.output_transcription_score is not None:
        parts.append(f"out={result.output_transcription_score:.0f}")
    if parts:
        tx_tag = f" [TX:{'/'.join(parts)}]"

    return latency_tag, glossary_tag, tx_tag, display


async def main():
    parser = argparse.ArgumentParser(description="Long-running translation soak test")
    parser.add_argument("--url", default="ws://localhost:8000", help="WebSocket base URL")
    parser.add_argument("--duration", type=int, default=3600, help="Test duration in seconds")
    parser.add_argument("--source", default="en", help="Source language code")
    parser.add_argument("--target", default="ja", help="Target language code")
    parser.add_argument(
        "--log",
        default=None,
        help="Path to JSONL log file for per-iteration metrics (default: auto-generated)",
    )
    args = parser.parse_args()

    ws_url = f"{args.url}/ws/soak-test/soak-session-001?source={args.source}&target={args.target}"

    genai_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    tts_client = texttospeech.TextToSpeechClient()
    stt_client = speech.SpeechClient()

    log_path = args.log or f"soak_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
    log_file = open(log_path, "a")

    stats = Stats()
    start = time.monotonic()
    glossary_cycle = iter(range(len(TEST_GLOSSARY)))

    print(f"[{stamp()}] Starting soak test: {args.source} -> {args.target}, duration={args.duration}s")
    print(f"[{stamp()}] Glossary: {len(TEST_GLOSSARY)} entries")
    print(f"[{stamp()}] Logging metrics to {log_path}")
    print(f"[{stamp()}] Connecting to {ws_url}")

    ssl_ctx = None
    if ws_url.startswith("wss://"):
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with websockets.connect(ws_url, ssl=ssl_ctx) as ws:
        await ws.send(json.dumps({"glossary": TEST_GLOSSARY}))
        print(f"[{stamp()}] Connected, setup sent with glossary")

        while time.monotonic() - start < args.duration:
            stats.iterations += 1

            # Alternate: every 3rd iteration is a glossary test
            glossary_entry = None
            if stats.iterations % 3 == 0:
                gi = next(glossary_cycle, None)
                if gi is None:
                    glossary_cycle = iter(range(len(TEST_GLOSSARY)))
                    gi = next(glossary_cycle)
                glossary_entry = TEST_GLOSSARY[gi]

            result = await run_iteration(
                ws, genai_client, tts_client, stt_client,
                stats.iterations, args.source, args.target,
                glossary_entry=glossary_entry,
            )
            stats.results.append(result)

            log_file.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "iteration": result.index,
                "original": result.original,
                "input_transcription": result.input_transcription,
                "output_transcription": result.output_transcription,
                "stt_transcription": result.stt_transcription,
                "passed": result.passed,
                "score": result.score,
                "reason": result.reason or None,
                "error": result.error,
                "elapsed_sec": round(result.elapsed, 2),
                "first_response_sec": round(result.first_response_sec, 2) if result.first_response_sec is not None else None,
                "turn_complete_sec": round(result.turn_complete_sec, 2) if result.turn_complete_sec is not None else None,
                "glossary_term": result.glossary_term,
                "glossary_found": result.glossary_found,
                "input_transcription_score": result.input_transcription_score,
                "output_transcription_score": result.output_transcription_score,
            }, ensure_ascii=False) + "\n")
            log_file.flush()

            # Latency stats (based on turn_complete — user-perceived latency)
            if result.turn_complete_sec is not None:
                if result.turn_complete_sec <= LATENCY_THRESHOLD:
                    stats.latency_ok += 1
                else:
                    stats.latency_slow += 1

            # Glossary stats
            if result.glossary_found is not None:
                stats.glossary_checked += 1
                if result.glossary_found:
                    stats.glossary_found += 1

            # Transcription quality stats
            if result.input_transcription_score is not None:
                stats.input_transcription_scores.append(result.input_transcription_score)
            if result.output_transcription_score is not None:
                stats.output_transcription_scores.append(result.output_transcription_score)

            latency_tag, glossary_tag, tx_tag, display = _format_tags(result)

            if result.error:
                stats.errors += 1
                print(
                    f"[{stamp()}] #{result.index} ERROR ({result.elapsed:.1f}s){latency_tag}{glossary_tag}{tx_tag} | "
                    f'"{result.original[:50]}" | {result.error}'
                )
            elif result.passed:
                stats.passed += 1
                stats.total_score += result.score
                print(
                    f"[{stamp()}] #{result.index} PASS ({result.score:.0f}/10) "
                    f'({result.elapsed:.1f}s){latency_tag}{glossary_tag}{tx_tag} | "{result.original[:50]}" -> "{display}"'
                )
            else:
                stats.failed += 1
                stats.total_score += result.score
                print(
                    f"[{stamp()}] #{result.index} FAIL ({result.score:.0f}/10) "
                    f'({result.elapsed:.1f}s){latency_tag}{glossary_tag}{tx_tag} | "{result.original[:50]}" -> "{display}"'
                    f" | {result.reason}"
                )

            elapsed = time.monotonic() - start
            remaining = args.duration - elapsed
            if remaining > 0:
                print(
                    f"         [{elapsed:.0f}s / {args.duration}s elapsed, "
                    f"{remaining:.0f}s remaining]",
                    flush=True,
                )

    # Summary
    elapsed = time.monotonic() - start
    scored = stats.passed + stats.failed
    avg_score = stats.total_score / scored if scored else 0
    report: list[str] = []

    report.append(f"\n[{stamp()}] === SUMMARY ===")
    report.append(
        f"Duration: {elapsed:.0f}s | Iterations: {stats.iterations} | "
        f"Passed: {stats.passed}/{stats.iterations} "
        f"({100 * stats.passed / stats.iterations:.1f}%) | "
        f"Avg score: {avg_score:.1f}/10 | Errors: {stats.errors}"
    )
    if stats.glossary_checked:
        report.append(
            f"Glossary: {stats.glossary_found}/{stats.glossary_checked} "
            f"({100 * stats.glossary_found / stats.glossary_checked:.1f}%) terms matched in output"
        )

    fr_latencies = [r.first_response_sec for r in stats.results if r.first_response_sec is not None]
    report.extend(_format_distribution("First Response (speech-end to first audio/transcript)", fr_latencies, [
        ("=0s", 0.0, 0.001),
        ("0-0.1s", 0.001, 0.1),
        ("0.1-0.5s", 0.1, 0.5),
        ("0.5-1s", 0.5, 1.0),
        ("1-2s", 1.0, 2.0),
        ("2-5s", 2.0, 5.0),
        (">5s", 5.0, 1e9),
    ]))

    tc_latencies = [r.turn_complete_sec for r in stats.results if r.turn_complete_sec is not None]
    report.extend(_format_distribution("Turn Complete (speech-end to full translation)", tc_latencies, [
        ("<2s", 0.0, 2.0),
        ("2-3s", 2.0, 3.0),
        ("3-4s", 3.0, 4.0),
        ("4-5s", 4.0, 5.0),
        ("5-7s", 5.0, 7.0),
        ("7-10s", 7.0, 10.0),
        (">10s", 10.0, 1e9),
    ]))

    scores = [r.score for r in stats.results if not r.error]
    report.extend(_format_distribution("Translation Score", scores, [
        ("0-2", 0.0, 2.5),
        ("3-4", 2.5, 4.5),
        ("5-6", 4.5, 6.5),
        ("7-8", 6.5, 8.5),
        ("9-10", 8.5, 10.1),
    ]))

    report.extend(_format_distribution("Input Transcription Score", stats.input_transcription_scores, [
        ("0-2", 0.0, 2.5),
        ("3-4", 2.5, 4.5),
        ("5-6", 4.5, 6.5),
        ("7-8", 6.5, 8.5),
        ("9-10", 8.5, 10.1),
    ]))

    report.extend(_format_distribution("Output Transcription Score", stats.output_transcription_scores, [
        ("0-2", 0.0, 2.5),
        ("3-4", 2.5, 4.5),
        ("5-6", 4.5, 6.5),
        ("7-8", 6.5, 8.5),
        ("9-10", 8.5, 10.1),
    ]))

    elapsed_vals = [r.elapsed for r in stats.results if not r.error]
    report.extend(_format_distribution("Total Iteration Time", elapsed_vals, [
        ("<10s", 0.0, 10.0),
        ("10-15s", 10.0, 15.0),
        ("15-20s", 15.0, 20.0),
        ("20-25s", 20.0, 25.0),
        ("25-30s", 25.0, 30.0),
        (">30s", 30.0, 1e9),
    ]))

    for line in report:
        print(line)

    report_path = log_path.replace(".jsonl", ".report")
    with open(report_path, "w") as f:
        f.write("\n".join(report) + "\n")

    log_file.close()
    print(f"[{stamp()}] Metrics log: {log_path}")
    print(f"[{stamp()}] Report: {report_path}")
    sys.exit(0 if stats.errors == 0 and stats.passed > 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
