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

UNDER LOAD IS THE ONLY TEST THAT MATTERS. An idle number proves the pipeline is
wired up; it says nothing about the guarantee, which is that the presence holds
its budget *while the bench is working*. `--load N` puts N concurrent deep
generations on mini's :8091 instance and measures the same turns against them,
then prints both halves and the drift between them. Baseline first, always —
the harness runs the idle pass itself rather than trusting a number from a
previous session, because the two passes have to share a warm prefix cache, the
same model residency, and the same afternoon.

Usage:
    python bench/voicebench.py --say "what is the context ceiling on mini" -n 20
    python bench/voicebench.py --wav sample.wav --mode vad -n 20
    python bench/voicebench.py --load 2 -n 20      # idle pass, then loaded pass
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

# DECLARED BUDGETS, in milliseconds to the first audible word.
#
# 500 in-home is the target, not a measurement. Recorded here so that a run
# either meets it or says by how much it missed — a target with no number
# attached is the one thing this repo does not do. Baseline on 2026-08-30 before
# any of the work aimed at it: median 742 ms, p95 1088 ms, and a spread from
# 416 ms to 1088 ms on ten consecutive turns that is mostly the wifi link to
# mini (ser5 -> mini ping: min 2.3 ms, avg 28.5 ms, max 229.7 ms).
#
# The remote number is deliberately WORSE and deliberately separate. A turn taken
# from a coffee shop crosses a WAN twice; pretending it is the same as the
# in-home budget would either make the in-home target meaningless or make remote
# look broken. Honestly worse beats falsely equal.
BUDGET_MS = {"home": 500.0, "remote": 1500.0}


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


# Long enough that a worker spends its time DECODING rather than reconnecting.
# A load generator that mostly opens sockets measures the network, not the GPU.
_LOAD_PROMPT = (
    "Explain, in as much detail as you can, how a static KV cache partition "
    "interacts with a prefix cache in a batched inference server. Cover eviction, "
    "slot residency, and what happens when one slot's context exceeds its share."
)


async def load_worker(
    base_url: str, model: str, stop: asyncio.Event, tokens: list[int]
) -> None:
    """Hold one slot on the deep instance busy until told to stop.

    Deliberately points at :8091, not :8090. The presence and the bench are two
    llama-server processes sharing one GPU, and the question this harness exists
    to answer is whether the second starves the first. Loading :8090 would only
    measure llama.cpp's own slot scheduling, which is a different question with a
    much less interesting answer.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as c:
        while not stop.is_set():
            n = 0
            try:
                async with c.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": _LOAD_PROMPT}],
                        "stream": True,
                        "max_tokens": 1024,
                        "temperature": 0.7,
                    },
                ) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode("utf-8", "replace")[:200]
                        print(f"  ! load worker: {r.status_code} {body}", file=sys.stderr)
                        return
                    async for line in r.aiter_lines():
                        if stop.is_set():
                            break
                        if line.startswith("data:") and "[DONE]" not in line:
                            n += 1
            except Exception as exc:  # noqa: BLE001 - the load is scenery, not the test
                print(f"  ! load worker: {exc}", file=sys.stderr)
                await asyncio.sleep(1.0)
            tokens.append(n)


async def with_load(base_url: str, model: str, workers: int):
    """Async context manager: N deep generations running for the duration."""
    stop = asyncio.Event()
    tokens: list[int] = []
    tasks = [
        asyncio.create_task(load_worker(base_url, model, stop, tokens))
        for _ in range(workers)
    ]
    # Let the load actually reach the GPU before the measured turns start.
    # Measuring during prefill would report contention that a steady-state
    # errand does not cause.
    await asyncio.sleep(5.0)
    return stop, tasks, tokens


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


def stats(rows: list[dict], key: str) -> tuple[float, float, float]:
    vals = sorted(r[key] for r in rows)
    p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
    return statistics.median(vals), statistics.mean(vals), p95


def report(rows: list[dict], mode: str, audio_s: float, label: str = "") -> None:
    if not rows:
        print("no successful turns", file=sys.stderr)
        raise SystemExit(1)

    def stat(key: str) -> tuple[float, float, float]:
        return stats(rows, key)

    print()
    suffix = f" [{label}]" if label else ""
    print(f"voicebench{suffix} — mode={mode}, n={len(rows)}, stimulus={audio_s:.2f}s of audio")
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


def judge_budget(rows: list[dict], budget_ms: float, profile: str) -> int:
    """Say whether the declared budget was met, and by how much if not.

    Prints a number either way. "It feels fast" is not a result, and neither is
    a target quietly revised downward to whatever the run happened to produce.
    """
    median, _, p95 = stats(rows, "total_ms")
    if median <= budget_ms:
        print(f"BUDGET MET ({profile}): median {median:.0f}ms <= {budget_ms:.0f}ms "
              f"target, p95 {p95:.0f}ms.")
        return 0
    print(f"BUDGET MISSED ({profile}): median {median:.0f}ms against a {budget_ms:.0f}ms "
          f"target — {median - budget_ms:.0f}ms over, p95 {p95:.0f}ms.")
    return 1


def compare(idle: list[dict], loaded: list[dict], workers: int, drift_pct: float) -> int:
    """Print the drift between the two passes and return a process exit code.

    The success criterion this implements, from the strategy: the same numbers
    hold while errands are running, within about 15%. It is stated as a
    threshold rather than a direction because a loaded pass that comes back
    FASTER is not a pass, it is evidence the load never reached the GPU.
    """
    print()
    print(f"under load — {workers} concurrent deep generations")
    print(f"{'stage':<28}{'idle':>10}{'loaded':>10}{'drift':>10}")
    print("-" * 58)
    worst = 0.0
    for key, label in [
        ("stt_ms", "STT finalize"),
        ("sentence_ms", "model -> first sentence"),
        ("tts_ms", "synthesis"),
        ("total_ms", "TO FIRST AUDIBLE WORD"),
    ]:
        a = stats(idle, key)[0]
        b = stats(loaded, key)[0]
        pct = ((b - a) / a * 100) if a else 0.0
        if key == "total_ms":
            worst = pct
        print(f"{label:<28}{a:>9.0f}ms{b:>9.0f}ms{pct:>9.1f}%")
    print()
    if worst > drift_pct:
        print(
            f"FAIL: first audible word drifted {worst:.1f}% under load, "
            f"budget is {drift_pct:.0f}%. The fast path is NOT immune to deep work."
        )
        return 1
    if worst < -drift_pct:
        print(
            f"SUSPECT: the loaded pass was {abs(worst):.1f}% FASTER. That is not a "
            f"pass — check the load workers actually reached mini."
        )
        return 1
    print(f"PASS: first audible word drifted {worst:.1f}%, budget is {drift_pct:.0f}%.")
    return 0


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
    # The deep instance, not the presence's. See load_worker for why.
    ap.add_argument("--load", type=int, default=0, metavar="N",
                    help="run a second pass with N concurrent deep generations")
    ap.add_argument("--load-url", default="http://mini:8091/v1")
    ap.add_argument("--load-model", default="qwen3.8-27b")
    ap.add_argument("--drift", type=float, default=15.0, metavar="PCT",
                    help="how far the loaded pass may drift before it fails")
    ap.add_argument("--profile", choices=sorted(BUDGET_MS), default="home",
                    help="which declared budget to judge against")
    ap.add_argument("--budget", type=float, default=None, metavar="MS",
                    help="override the profile's budget for first audible word")
    args = ap.parse_args()
    budget = args.budget if args.budget is not None else BUDGET_MS[args.profile]

    pcm = load_wav(args.wav) if args.wav else await synth_prompt(args.tts_url, args.say, args.tts_model, args.voice)
    audio_s = len(pcm) / 2 / RATE
    print(f"stimulus: {audio_s:.2f}s ({'wav' if args.wav else 'synthesised'})")

    async def pass_(label: str) -> list[dict]:
        rows: list[dict] = []
        for i in range(args.n):
            row = await one_turn(args.url, pcm, args.mode, args.timeout)
            if row:
                rows.append(row)
                print(f"  {label} {i + 1:>3}/{args.n}  {row['total_ms']:.0f}ms")
            else:
                print(f"  {label} {i + 1:>3}/{args.n}  FAILED")
            # The first turn pays a cold prefix on mini. Later turns should hit
            # the prompt cache; a run where they do not is itself the finding.
            await asyncio.sleep(0.5)
        return rows

    idle = await pass_("idle ")
    report(idle, args.mode, audio_s, label="idle" if args.load else "")
    verdict = judge_budget(idle, budget, args.profile)
    if not args.load:
        raise SystemExit(verdict)

    # Baseline first, in the SAME run. Comparing today's loaded pass against a
    # number recorded last week would fold in a different prefix-cache state, a
    # different model residency and a different room temperature, and attribute
    # all of it to the load.
    print(f"\nstarting {args.load} deep generations on {args.load_url} ...")
    stop, tasks, tokens = await with_load(args.load_url, args.load_model, args.load)
    try:
        loaded = await pass_("load ")
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    report(loaded, args.mode, audio_s, label=f"under load x{args.load}")
    verdict |= judge_budget(loaded, budget, f"{args.profile}, under load")
    raise SystemExit(verdict | compare(idle, loaded, args.load, args.drift))


if __name__ == "__main__":
    asyncio.run(main())
