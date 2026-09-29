"""Headless doubles for the desktop client e2e suite.

No PortAudio, no pynput, no speakers. The client talks to either a scripted
websocket (protocol tests) or a real Session behind uvicorn (full-loop tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import time
from typing import Any

import uvicorn
import websockets

from voice_client import FRAME_SAMPLES, Client

FRAME_BYTES = FRAME_SAMPLES * 2  # 16 kHz int16 mono, 20 ms


class FakePlayback:
    """A speaker that records rather than plays.

    `drain()` moves the jitter buffer into `played` — that is the production
    contract after `done`. `flush()` drops it, which is barge-in. The suite
    asserts the two are not mixed up.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.played = bytearray()
        self.flushes = 0

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    def queued_bytes(self) -> int:
        return len(self.buffer)

    def flush(self) -> None:
        self.flushes += 1
        self.buffer.clear()

    async def drain(self) -> None:
        self.played.extend(self.buffer)
        self.buffer.clear()

    def close(self) -> None:
        pass

    @property
    def heard(self) -> bytes:
        """Bytes written to the speaker, whether or not drain() has run.

        Production Playback is consumed by a PortAudio callback that runs
        even between turns. This double has no callback, so returning-errand
        audio sits in `buffer` until the next drain. Tests that ask "did the
        user hear it" should look here, not at `played` alone.
        """
        return bytes(self.played) + bytes(self.buffer)


class FakeLab:
    """A websocket that speaks the presence protocol, under test control.

    Auto-replies `ready` to `hello`. Everything after that is sent by the
    test, so a scenario is a script rather than a guess at timing.
    """

    def __init__(
        self,
        *,
        conversation: str = "conv-test",
        resumed: int = 0,
        working: int = 0,
        waiting: int = 0,
    ) -> None:
        self.conversation = conversation
        self.resumed = resumed
        self.working = working
        self.waiting = waiting
        self.inbox: list[bytes | str] = []
        self.hello: dict | None = None
        self.ws = None
        self._server = None
        self.url = ""
        self.connected = asyncio.Event()

    async def __aenter__(self) -> FakeLab:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        # Path is ignored by this server; the real gateway uses /v1/stream.
        self.url = f"ws://127.0.0.1:{port}/v1/stream"
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws) -> None:
        self.ws = ws
        self.connected.set()
        try:
            async for msg in ws:
                self.inbox.append(msg)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    if data.get("type") == "hello":
                        self.hello = data
                        await ws.send(json.dumps({
                            "type": "ready",
                            "conversation": self.conversation,
                            "resumed": self.resumed,
                            "working": self.working,
                            "waiting": self.waiting,
                        }))
        except websockets.ConnectionClosed:
            pass

    async def send_json(self, payload: dict) -> None:
        assert self.ws is not None, "no client connected"
        await self.ws.send(json.dumps(payload))

    async def send_pcm(self, data: bytes) -> None:
        assert self.ws is not None, "no client connected"
        await self.ws.send(data)

    async def close_socket(self) -> None:
        if self.ws is not None:
            await self.ws.close()

    async def wait_inbox(self, pred, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred(self.inbox):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"inbox never matched: {self.inbox!r}")


class _Uvicorn(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # pragma: no cover - test helper
        pass


async def start_gateway(app) -> tuple[_Uvicorn, asyncio.Task, str]:
    """Run a real Session over a real websocket, no TestClient in between."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"
    )
    server = _Uvicorn(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        raise RuntimeError("uvicorn did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, f"ws://127.0.0.1:{port}/v1/stream"


async def stop_gateway(server: _Uvicorn, task: asyncio.Task) -> None:
    server.should_exit = True
    await asyncio.wait_for(task, timeout=5)


def silence(n_frames: int = 3) -> bytes:
    return b"\x00" * FRAME_BYTES * n_frames


def start_client(url: str, play: FakePlayback, **kwargs) -> tuple[Client, asyncio.Event, asyncio.Task]:
    client = Client(url, play, **kwargs)
    stop = asyncio.Event()
    task = asyncio.create_task(client.run(stop))
    return client, stop, task


async def wait_up(client: Client, timeout: float = 2.0) -> None:
    await asyncio.wait_for(client.up.wait(), timeout=timeout)


async def ptt(client: Client, frames: int = 5) -> None:
    """Hold, speak, release.

    Frames go in AFTER the turn starts: `Client.run` drops the mic queue at
    trigger, the same way production drops audio from before the key went down.
    """
    client.hold.set()
    client.trigger.put("ptt")
    deadline = time.monotonic() + 2
    while client.idle.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    if client.idle.is_set():
        raise AssertionError("turn never started")
    for _ in range(frames):
        _put(client.mic, b"\x01" * FRAME_BYTES)
    client.hold.clear()
    # One more frame so the turn loop notices the key is up and sends `end`.
    _put(client.mic, b"\x01" * FRAME_BYTES)


def keep_feeding(client: Client) -> asyncio.Task:
    """The real mic callback never stops. Feed until the turn goes idle."""

    async def _feed() -> None:
        while not client.idle.is_set():
            _put(client.mic, b"\x02" * FRAME_BYTES)
            await asyncio.sleep(0.02)

    return asyncio.create_task(_feed())


async def wait_idle(client: Client, timeout: float = 3.0) -> None:
    await asyncio.wait_for(client.idle.wait(), timeout=timeout)


async def wait_event(
    client: Client, typ: str, timeout: float = 3.0, *, min_count: int = 1
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = events_of(client, typ)
        if len(matches) >= min_count:
            return matches[min_count - 1]
        await asyncio.sleep(0.02)
    events = client.presence.events if client.presence else []
    raise AssertionError(f"wanted {min_count} {typ!r}, have {events!r}")


def events_of(client: Client, typ: str) -> list[dict]:
    if client.presence is None:
        return []
    return [e for e in client.presence.events if e.get("type") == typ]


def inbox_types(lab: FakeLab) -> list[str]:
    out = []
    for item in lab.inbox:
        if isinstance(item, bytes):
            out.append("pcm")
        else:
            out.append(json.loads(item).get("type", "?"))
    return out


def _put(q: queue.Queue, item: Any) -> None:
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


async def shutdown(client: Client, stop: asyncio.Event, task: asyncio.Task) -> None:
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
