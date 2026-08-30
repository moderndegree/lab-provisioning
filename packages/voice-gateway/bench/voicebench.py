#!/usr/bin/env python3
"""End-to-end latency measurement for the voice loop.

This exists because the repo settles arguments with numbers. Every latency
figure in the plan and the docs is marked `(est.)` until this has run, per
docs/operating-manual.md: "Speed figures marked `(est.)` stay marked until
measured" and "Adopt a model change only on a measured win."

WHAT IT MEASURES, from the client's side of the socket — which is the only
vantage point that matches what a person experiences:

    t0  endpoint          we send {"type":"end"} (ptt) or stop sending (vad)
    t1  final transcript  STT finalize                          t1-t0
    t2  first sentence    model produced a speakable sentence   t2-t1
    t3  first audio byte  synthesis of that sentence            t3-t2
                          ───────────────────────────────────────────────
                          time to first audible word            t3-t0  <-- THE number

t2-t1 is time-to-first-SENTENCE, not time-to-first-token. It is deliberately not
labelled TTFT: it includes however many tokens the model needed before the
chunker had something worth speaking, and that is the quantity the listener
actually waits through.

THE AUDIO IS PACED IN REAL TIME. Blasting a WAV at the gateway as fast as the
socket accepts it would let the STT finish transcribing before "the user" stopped
talking, and report a finalize time that no live turn can ever achieve. Frames go
out on a 20 ms wall clock, exactly like a microphone.

Usage:
    python bench/voicebench.py --say "what is the context ceiling on mini" -n 20
    python bench/voicebench.py --wav sample.wav --mode vad -n 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import httpx
import websockets

FRAME_MS = 20
RATE = 16_000
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2


def load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
            raise SystemExit(
                f"{path}: need 16 kHz mono 16-bit, got {w.getframerate()} Hz "
                f"{w.getnchannels()}ch {w.getsampwidth() * 8}-bit"
            )
        return w.readframes(w.getnframes())


async def synth_prompt(tts_url: str, text: str, model: str, voice: str) -> bytes:
    """Generate the test utterance with the lab's own TTS, then replay it.

    Avoids shipping WAV fixtures, and gives every run byte-identical input so
    two runs differ only in the system under test.

    The TTS emits 24 kHz (measured); the gateway wants 16 kHz. Decimation by
    nearest sample is crude and would be wrong for a product, but this is a
    fixed, repeatable stimulus for a TIMING harness. It is explicitly not an ASR
    accuracy test: a TTS-to-STT round trip mispronounces proper nouns ("mini"
    comes back as "many"), so judge words from a real microphone, not from here.
    """
    import array

    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            f"{tts_url.rstrip('/')}/v1/audio/speech",
            json={
                "model": model,
                "input": text,
                "voice": voice,
                "response_format": "pcm",
                "speed": 1.0,
            },
        )
        r.raise_for_status()
        src = array.array("h")
        src.frombytes(r.content[: len(r.content) // 2 * 2])

    ratio = 24_000 / RATE
    out = array.array("h", (src[min(int(i * ratio), len(src) - 1)] for i in range(int(len(src) / ratio))))
    return out.tobytes()


async def one_turn(url: str, pcm: bytes, mode: str, timeout: float) -> dict | None:
    async with websockets.connect(url, compression=None, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device": "voicebench", "mode": mode, "sample_rate": RATE}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("type") == "ready":
                break

        # Pace the audio like a microphone would deliver it.
        start = time.perf_counter()
        for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
            await ws.send(pcm[i : i + FRAME_BYTES])
            target = start + (i / FRAME_BYTES + 1) * FRAME_MS / 1000
            drift = target - time.perf_counter()
            if drift > 0:
                await asyncio.sleep(drift)

        if mode == "ptt":
            await ws.send(json.dumps({"type": "end"}))
            t0 = time.perf_counter()
        else:
            # In vad mode the gateway endpoints on its own after
            # VOICE_VAD_SILENCE_MS of quiet, so feed real silence and start the
            # clock at the moment the speech stopped.
            t0 = time.perf_counter()
            silence = b"\x00" * FRAME_BYTES
            for _ in range(40):  # 800 ms, comfortably past the hangover
                await ws.send(silence)
                await asyncio.sleep(FRAME_MS / 1000)

        marks: dict[str, float] = {}
        transcript = ""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.perf_counter()))
            except TimeoutError:
                break
            now = time.perf_counter()
            if isinstance(raw, bytes):
                marks.setdefault("first_audio", now)
                break  # first audible sample is the number; stop the clock
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "final":
                marks.setdefault("final", now)
                transcript = msg.get("text", "")
            elif kind == "speaking":
                marks.setdefault("first_sentence", now)
            elif kind == "error":
                print(f"  ! gateway error: {msg.get('message')}", file=sys.stderr)
                return None

        if "first_audio" not in marks:
            return None
        return {
            "stt_ms": (marks.get("final", marks["first_audio"]) - t0) * 1000,
            "sentence_ms": (marks.get("first_sentence", marks["first_audio"]) - marks.get("final", t0)) * 1000,
            "tts_ms": (marks["first_audio"] - marks.get("first_sentence", t0)) * 1000,
            "total_ms": (marks["first_audio"] - t0) * 1000,
            "transcript": transcript,
        }


def report(rows: list[dict], mode: str, audio_s: float) -> None:
    if not rows:
        print("no successful turns", file=sys.stderr)
        raise SystemExit(1)

    def stat(key: str) -> tuple[float, float, float]:
        vals = sorted(r[key] for r in rows)
        p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        return statistics.median(vals), statistics.mean(vals), p95

    print()
    print(f"voicebench — mode={mode}, n={len(rows)}, stimulus={audio_s:.2f}s of audio")
    print(f"{'stage':<28}{'median':>10}{'mean':>10}{'p95':>10}")
    print("-" * 58)
    for key, label in [
        ("stt_ms", "STT finalize"),
        ("sentence_ms", "model -> first sentence"),
        ("tts_ms", "synthesis"),
        ("total_ms", "TO FIRST AUDIBLE WORD"),
    ]:
        med, mean, p95 = stat(key)
        print(f"{label:<28}{med:>9.0f}ms{mean:>9.0f}ms{p95:>9.0f}ms")
    print()
    print(f"transcript: {rows[-1]['transcript']!r}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8772/v1/stream")
    ap.add_argument("--tts-url", default="http://127.0.0.1:8770")
    ap.add_argument("--mode", choices=["ptt", "vad"], default="ptt")
    ap.add_argument("--say", default="what is the context ceiling on mini")
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--tts-model", default="speaches-ai/piper-en_US-amy-medium")
    ap.add_argument("--voice", default="en_US-amy-medium")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    pcm = load_wav(args.wav) if args.wav else await synth_prompt(args.tts_url, args.say, args.tts_model, args.voice)
    audio_s = len(pcm) / 2 / RATE
    print(f"stimulus: {audio_s:.2f}s ({'wav' if args.wav else 'synthesised'})")

    rows: list[dict] = []
    for i in range(args.n):
        row = await one_turn(args.url, pcm, args.mode, args.timeout)
        if row:
            rows.append(row)
            print(f"  {i + 1:>3}/{args.n}  {row['total_ms']:.0f}ms")
        else:
            print(f"  {i + 1:>3}/{args.n}  FAILED")
        # The first turn pays a cold prefix on mini. Later turns should hit the
        # prompt cache; a run where they do not is itself the finding.
        await asyncio.sleep(0.5)

    report(rows, args.mode, audio_s)


if __name__ == "__main__":
    asyncio.run(main())
