"""The wire protocol between a voice client and the gateway.

This module is the CONTRACT. The Windows client, the bench harness, and any
future ESP32 satellite all speak exactly this and nothing else, which is why it
has no dependencies and no behaviour — just the vocabulary.

Audio in  : 16 kHz mono int16 PCM, little-endian, 20 ms frames (320 samples,
            640 bytes). 16 kHz because that is what Whisper wants; resampling
            anywhere else in the path is wasted latency.
Audio out : int16 PCM at TTS_SAMPLE_RATE. We ask for response_format=pcm
            precisely so nothing in this path has to decode a container — the
            bytes go socket to socket.

Client -> gateway
    text  {"type":"hello","device":str,"mode":"ptt"|"vad","sample_rate":16000}
    bin   PCM frames
    text  {"type":"end"}     end of utterance. In ptt mode this IS the endpoint
                             and skips the VAD hangover entirely.
    text  {"type":"cancel"}  barge-in / abort whatever is in flight

Gateway -> client
    text  {"type":"ready"}
    text  {"type":"partial","text":str}      live, not yet committed
    text  {"type":"final","text":str}        what we actually sent to the model
    text  {"type":"speaking","seq":int,"text":str}
                                             precedes that sentence's audio
    bin   PCM frames for the sentence just announced
    text  {"type":"done"}
    text  {"type":"cancelled"}
    text  {"type":"notice","text":str}       out-of-band, e.g. a delegated
                                             result arriving later
    text  {"type":"error","message":str}
"""

from __future__ import annotations

from typing import Any, Literal

# ─── Audio ───────────────────────────────────────────────────────────────────
INPUT_SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = INPUT_SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2  # int16

# MEASURED, not assumed. speaches returns 24000 Hz for piper voices even though
# en_US-amy-medium is natively 22050 — it resamples on the way out. Verified on
# ser5 2026-08-30 by reading the WAV header and confirming the raw PCM byte
# count agrees with it.
#
# It has to be hard-coded because `response_format: "pcm"` comes back as bare
# `content-type: audio/pcm` with NO rate parameter, so there is nothing to parse.
# Getting it wrong does not error anywhere: the audio simply plays at the wrong
# pitch and speed. roles/voice/tasks/verify.yml asserts it against the WAV
# header so a future image bump that changes the rate is caught by `make verify`
# rather than by the assistant suddenly sounding like a chipmunk.
TTS_SAMPLE_RATE = 24_000

Mode = Literal["ptt", "vad"]

# ─── Client -> gateway ───────────────────────────────────────────────────────
C_HELLO = "hello"
C_END = "end"
C_CANCEL = "cancel"

# ─── Gateway -> client ───────────────────────────────────────────────────────
S_READY = "ready"
S_PARTIAL = "partial"
S_FINAL = "final"
S_SPEAKING = "speaking"
S_DONE = "done"
S_CANCELLED = "cancelled"
S_NOTICE = "notice"
S_ERROR = "error"


def ready() -> dict[str, Any]:
    return {"type": S_READY}


def partial(text: str) -> dict[str, Any]:
    return {"type": S_PARTIAL, "text": text}


def final(text: str) -> dict[str, Any]:
    return {"type": S_FINAL, "text": text}


def speaking(seq: int, text: str) -> dict[str, Any]:
    return {"type": S_SPEAKING, "seq": seq, "text": text}


def done() -> dict[str, Any]:
    return {"type": S_DONE}


def cancelled() -> dict[str, Any]:
    return {"type": S_CANCELLED}


def notice(text: str) -> dict[str, Any]:
    return {"type": S_NOTICE, "text": text}


def error(message: str) -> dict[str, Any]:
    return {"type": S_ERROR, "message": message}
