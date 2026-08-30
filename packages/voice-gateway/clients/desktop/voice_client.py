#!/usr/bin/env python3
"""Desktop voice client — the interface to the lab from the Windows workstation.

Two ways to start a turn, both available at once:

  push-to-talk   hold a hotkey (default ctrl+alt+space). Key release IS the
                 endpoint, which skips the gateway's VAD hangover entirely and
                 is the fastest path there is.
  wake word      openWakeWord runs locally; the gateway's VAD decides when you
                 stopped. Hands-free, costs the hangover.

ONE CONNECTION PER TURN, opened at the moment of intent — key press, or wake
word firing — which is before you have finished speaking. Connection setup is
therefore off the critical path entirely, and each turn gets the endpointing
mode it actually wants instead of a compromise. A persistent socket would have
to pick one mode for both, and in vad mode a mid-sentence pause during a
push-to-talk hold would cut you off.

The mic KEEPS STREAMING while the assistant talks. That is not an oversight: it
is what makes barge-in work. Speaking over the answer cancels it.

Install (on the workstation, not ser5):
    pip install -e "packages/voice-gateway[client]"
    python packages/voice-gateway/clients/desktop/voice_client.py --host ser5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys
import threading
from collections import deque

import numpy as np
import sounddevice as sd
import websockets

IN_RATE = 16_000
OUT_RATE = 24_000  # measured: speaches emits 24 kHz for piper voices (protocol.TTS_SAMPLE_RATE)
FRAME_MS = 20
FRAME_SAMPLES = IN_RATE * FRAME_MS // 1000
WAKE_CHUNK = 1280  # 80 ms — openWakeWord's expected hop


class Playback:
    """Output stream with a jitter buffer.

    The gateway pushes audio as fast as it can synthesise it, which is far
    faster than real time, so buffering is required. `flush()` is the barge-in
    behaviour: drop everything not yet played, immediately.
    """

    def __init__(self) -> None:
        self._buf = deque()
        self._lock = threading.Lock()
        self._stream = sd.RawOutputStream(
            samplerate=OUT_RATE,
            channels=1,
            dtype="int16",
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status) -> None:
        want = frames * 2
        out = bytearray()
        with self._lock:
            while self._buf and len(out) < want:
                out += self._buf.popleft()
            if len(out) > want:
                self._buf.appendleft(bytes(out[want:]))
                out = out[:want]
        if len(out) < want:
            out += b"\x00" * (want - len(out))
        outdata[:] = bytes(out)

    def write(self, data: bytes) -> None:
        with self._lock:
            self._buf.append(data)

    def flush(self) -> None:
        with self._lock:
            self._buf.clear()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class Hotkey:
    """Global push-to-talk. Tracks the pressed set rather than using pynput's
    GlobalHotKeys, which fires on press only and gives no release edge."""

    def __init__(self, combo: str, on_press, on_release) -> None:
        from pynput import keyboard

        self._kb = keyboard
        self._want = {c.strip().lower() for c in combo.split("+") if c.strip()}
        self._down: set[str] = set()
        self._active = False
        self._on_press = on_press
        self._on_release = on_release
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.start()

    def _name(self, key) -> str | None:
        if isinstance(key, self._kb.KeyCode) and key.char:
            return key.char.lower()
        if isinstance(key, self._kb.Key):
            return key.name.lower().replace("_l", "").replace("_r", "").replace("_gr", "")
        return None

    def _press(self, key) -> None:
        name = self._name(key)
        if not name:
            return
        self._down.add(name)
        if not self._active and self._want <= self._down:
            self._active = True
            self._on_press()

    def _release(self, key) -> None:
        name = self._name(key)
        if not name:
            return
        self._down.discard(name)
        if self._active and not self._want <= self._down:
            self._active = False
            self._on_release()

    def stop(self) -> None:
        self._listener.stop()


class WakeWord:
    def __init__(self, model: str, threshold: float) -> None:
        from openwakeword.model import Model

        self._model = Model(wakeword_models=[model])
        self._threshold = threshold
        self._pending = np.zeros(0, dtype=np.int16)

    def fired(self, frame: bytes) -> bool:
        self._pending = np.concatenate((self._pending, np.frombuffer(frame, dtype=np.int16)))
        hit = False
        while self._pending.shape[0] >= WAKE_CHUNK:
            chunk, self._pending = self._pending[:WAKE_CHUNK], self._pending[WAKE_CHUNK:]
            scores = self._model.predict(chunk)
            if any(s >= self._threshold for s in scores.values()):
                hit = True
        if hit:
            self._model.reset()
        return hit


async def do_turn(url: str, mode: str, mic: queue.Queue, play: Playback, hold: threading.Event) -> None:
    """One connection, one turn: stream, listen, keep the mic open for barge-in."""
    async with websockets.connect(url, compression=None, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device": "workstation", "mode": mode, "sample_rate": IN_RATE}))
        finished = asyncio.Event()
        ended = False

        async def pump() -> None:
            nonlocal ended
            loop = asyncio.get_running_loop()
            while not finished.is_set():
                try:
                    frame = await loop.run_in_executor(None, mic.get, True, 0.1)
                except queue.Empty:
                    continue
                try:
                    await ws.send(frame)
                except websockets.ConnectionClosed:
                    return
                # ptt: the key came up. Send the endpoint once, then keep
                # streaming so barge-in still works during the answer.
                if mode == "ptt" and not ended and not hold.is_set():
                    ended = True
                    await ws.send(json.dumps({"type": "end"}))

        async def recv() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    play.write(raw)
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "partial":
                    print(f"\r  ... {msg['text']:<70}", end="", flush=True)
                elif kind == "final":
                    print(f"\r  you: {msg['text']:<70}")
                elif kind == "speaking":
                    print(f"  lab: {msg['text']}")
                elif kind == "notice":
                    print(f"  [hermes] {msg['text'][:500]}")
                elif kind == "cancelled":
                    play.flush()
                    print("  (interrupted)")
                    finished.set()
                    return
                elif kind == "error":
                    print(f"  ! {msg['message']}", file=sys.stderr)
                    finished.set()
                    return
                elif kind == "done":
                    finished.set()
                    return

        pump_task = asyncio.create_task(pump())
        try:
            await recv()
        finally:
            finished.set()
            pump_task.cancel()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="ser5")
    ap.add_argument("--port", type=int, default=8772)
    ap.add_argument("--hotkey", default="ctrl+alt+space")
    ap.add_argument("--wake-word", default="", help="openWakeWord model name, e.g. hey_jarvis. Empty disables.")
    ap.add_argument("--wake-threshold", type=float, default=0.5)
    args = ap.parse_args()

    url = f"ws://{args.host}:{args.port}/v1/stream"
    mic: queue.Queue = queue.Queue(maxsize=200)
    hold = threading.Event()
    trigger: queue.Queue = queue.Queue(maxsize=4)
    wake = WakeWord(args.wake_word, args.wake_threshold) if args.wake_word else None

    def with_suppress(fn, *a) -> None:
        try:
            fn(*a)
        except queue.Full:
            pass

    def on_audio(indata, _frames, _time, _status) -> None:
        frame = bytes(indata)
        # Always feed the wake detector, but never while a turn is running —
        # the assistant's own voice coming back through the speakers would
        # otherwise retrigger it.
        if wake and not hold.is_set() and trigger.empty() and wake.fired(frame):
            with_suppress(trigger.put_nowait, "vad")
        with_suppress(mic.put_nowait, frame)

    def ptt_down() -> None:
        hold.set()
        with_suppress(trigger.put_nowait, "ptt")

    stream = sd.RawInputStream(
        samplerate=IN_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES, callback=on_audio
    )
    stream.start()
    play = Playback()
    keys = Hotkey(args.hotkey, ptt_down, hold.clear)

    print(f"connected surface: {url}")
    print(f"push-to-talk: hold {args.hotkey}")
    print(f"wake word:    {args.wake_word or 'disabled'}")
    print("ctrl+c to quit\n")

    loop = asyncio.get_running_loop()
    try:
        while True:
            mode = await loop.run_in_executor(None, trigger.get)
            while not mic.empty():  # drop pre-trigger audio
                mic.get_nowait()
            try:
                await do_turn(url, mode, mic, play, hold)
            except (OSError, websockets.WebSocketException) as exc:
                print(f"  ! gateway unreachable: {exc}", file=sys.stderr)
            play.flush()
    except KeyboardInterrupt:
        pass
    finally:
        keys.stop()
        stream.stop()
        stream.close()
        play.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
