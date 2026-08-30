"""Driving a session the way a push-to-talk client does."""

from __future__ import annotations

import json

from voice_gateway import protocol as p

TERMINAL = {"done", "error", "cancelled"}


def hello(ws, device: str = "workstation", speaker: str | None = None) -> dict:
    frame = {"type": "hello", "device": device, "mode": "ptt", "sample_rate": 16000}
    if speaker:
        frame["speaker"] = speaker
    ws.send_text(json.dumps(frame))
    return json.loads(ws.receive()["text"])


def turn(ws, said: str | None = None, stt=None) -> list[dict]:
    """One push-to-talk turn: hold, release, listen until the floor is free.

    Sends exactly one frame and then `end`, because in ptt mode the key release
    IS the endpoint — there is no VAD hangover to wait out, which is the whole
    reason ptt is the fast path.
    """
    if said is not None and stt is not None:
        stt.queue.append(said)
    ws.send_bytes(b"\x00" * p.FRAME_BYTES)
    ws.send_text(json.dumps({"type": "end"}))
    out: list[dict] = []
    while True:
        msg = ws.receive()
        if msg.get("text") is None:
            continue
        frame = json.loads(msg["text"])
        out.append(frame)
        if frame["type"] in TERMINAL:
            return out


def spoken(frames: list[dict]) -> list[str]:
    return [f["text"] for f in frames if f["type"] == "speaking"]
