#!/usr/bin/env python3
"""Desktop voice client — the interface to the lab from the Windows workstation.

Two ways to start a turn, both available at once:

  push-to-talk   hold a hotkey (default ctrl+alt+space). Key release IS the
                 endpoint, which skips the gateway's VAD hangover entirely and
                 is the fastest path there is.
  wake word      openWakeWord runs locally; the gateway's VAD decides when you
                 stopped. Hands-free, costs the hangover.

ONE CONNECTION, held for the life of the client. It used to be one connection
per turn, on the reasoning that connection setup was then off the critical path
and each turn could pick its own endpointing mode. The first half was solved
better by never disconnecting at all, and the second by moving `mode` from the
handshake onto a per-turn `start` frame — so a push-to-talk hold and a wake-word
turn still get opposite endpointing over the same socket.

What the per-turn socket could not do is RECEIVE. An errand that finishes twenty
minutes later has to come back through the same voice at a conversational seam,
and there was no socket open to come back through. Two sockets — one for turns,
one to listen on — would have meant two things that could speak at once, which is
the one thing this system is built never to have.

The mic KEEPS STREAMING while the assistant talks. That is not an oversight: it
is what makes barge-in work. Speaking over the answer cancels it. Between turns
it stops: silence on the wire is what tells the gateway the floor is free, and
that is what a returning result waits for.

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


class Presence:
    """One socket, many turns, and whatever the lab says between them."""

    def __init__(self, ws, play: Playback, device: str, speaker: str) -> None:
        self._ws = ws
        self._play = play
        self.device = device
        self.speaker = speaker
        # Set when the floor comes free. A turn waits on it; between turns the
        # receive loop keeps running so a returning errand can still be heard.
        self._floor = asyncio.Event()

    async def hello(self) -> dict:
        frame = {"type": "hello", "device": self.device, "mode": "ptt",
                 "sample_rate": IN_RATE}
        if self.speaker:
            frame["speaker"] = self.speaker
        await self._ws.send(json.dumps(frame))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("type") == "ready":
                return msg

    async def receive_forever(self) -> None:
        """The only place anything is read. Runs for the life of the connection."""
        async for raw in self._ws:
            if isinstance(raw, bytes):
                self._play.write(raw)
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "partial":
                print(f"\r  ... {msg['text']:<70}", end="", flush=True)
            elif kind == "final":
                print(f"\r  you: {msg['text']:<70}")
            elif kind == "speaking":
                print(f"  lab: {msg['text']}")
            elif kind == "brief":
                print(f"  [errand] {msg['statement']}")
            elif kind == "working":
                print(f"  [{msg['n']} errand(s) in flight]")
            elif kind == "notice":
                print(f"  [detail] {msg['text'][:500]}")
            elif kind == "cancelled":
                self._play.flush()
                print("  (interrupted)")
                self._floor.set()
            elif kind == "error":
                print(f"  ! {msg['message']}", file=sys.stderr)
                self._floor.set()
            elif kind == "done":
                self._floor.set()

    async def turn(self, mode: str, mic: queue.Queue, hold: threading.Event) -> None:
        """Stream one utterance and wait for the floor to come back."""
        self._floor.clear()
        await self._ws.send(json.dumps({"type": "start", "mode": mode}))
        ended = False
        loop = asyncio.get_running_loop()
        while not self._floor.is_set():
            try:
                frame = await loop.run_in_executor(None, mic.get, True, 0.1)
            except queue.Empty:
                continue
            await self._ws.send(frame)
            # ptt: the key came up. Send the endpoint once, then keep streaming
            # so barge-in still works during the answer.
            if mode == "ptt" and not ended and not hold.is_set():
                ended = True
                await self._ws.send(json.dumps({"type": "end"}))
        # The mic stops here. Silence on the wire is how the gateway knows the
        # floor is free, and a finished errand waits for exactly that.


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="ser5")
    ap.add_argument("--port", type=int, default=8772)
    ap.add_argument("--hotkey", default="ctrl+alt+space")
    ap.add_argument("--wake-word", default="", help="openWakeWord model name, e.g. hey_jarvis. Empty disables.")
    ap.add_argument("--wake-threshold", type=float, default=0.5)
    # Recorded on every turn and every brief in the Ledger. There is one user and
    # the answer is always the same, which is exactly why it is worth sending
    # now: isolation can be built later on top of attributed history, and
    # attribution cannot be recovered retroactively.
    ap.add_argument("--device", default="workstation", help="how this client is named in the ledger")
    ap.add_argument("--speaker", default="", help="who is talking; blank uses the lab default")
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

    async def serve() -> None:
        """Hold one connection, take turns on it, until it drops."""
        async with websockets.connect(url, compression=None, max_size=None) as ws:
            presence = Presence(ws, play, args.device, args.speaker)
            ready = await presence.hello()
            note = []
            if ready.get("resumed"):
                note.append(f"{ready['resumed']} turns resumed")
            if ready.get("working"):
                note.append(f"{ready['working']} errand(s) in flight")
            if ready.get("waiting"):
                note.append(f"{ready['waiting']} result(s) waiting")
            print(f"  connected{' — ' + ', '.join(note) if note else ''}")
            # Receiving runs for the whole connection, not just during a turn:
            # that is what lets an errand come back twenty minutes later.
            listener = asyncio.create_task(presence.receive_forever())
            try:
                while not listener.done():
                    mode = await loop.run_in_executor(None, trigger.get)
                    while not mic.empty():  # drop pre-trigger audio
                        mic.get_nowait()
                    await presence.turn(mode, mic, hold)
                    play.flush()
            finally:
                listener.cancel()

    try:
        while True:
            try:
                await serve()
            except (OSError, websockets.WebSocketException) as exc:
                # Both boxes are wifi-only, so a dropped socket is routine rather
                # than exotic. Reconnecting is not a fallback path — it is the
                # normal one, and the conversation survives it because the Ledger
                # holds it, not this process.
                print(f"  ! gateway unreachable: {exc}; retrying", file=sys.stderr)
                await asyncio.sleep(2.0)
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
