"""Fixtures shared by the voice-gateway tests.

The tests here are deliberately narrow. They do not try to prove the voice loop
sounds good — that is what `bench/voicebench.py` and a real microphone are for,
and no assertion can stand in for either. What they DO cover is the part that
cannot be judged by listening: the state machine, the Ledger, and the ordering
between them. Both bugs these tests were written for presented identically to a
person — "sometimes it just doesn't hear me" — and neither produced an error
anywhere.

The VAD graph is the one real dependency. It is 2.3 MB and the role already
fetches it with a checksum; the tests skip themselves rather than download it.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket

from voice_gateway.config import Config
from voice_gateway.delivery import Returner
from voice_gateway.doorman import Doorman
from voice_gateway.ledger import Ledger
from voice_gateway.session import Deps, Session

# Where the role puts it on ser5, and where a developer can drop a copy.
_VAD_CANDIDATES = [
    os.environ.get("VOICE_VAD_MODEL_PATH", ""),
    "/data/services/voice/models/silero_vad.onnx",
    str(Path(__file__).parent / "silero_vad.onnx"),
]


@pytest.fixture(scope="session")
def vad_model() -> str:
    for path in _VAD_CANDIDATES:
        if path and Path(path).is_file():
            return path
    pytest.skip(
        "silero_vad.onnx not found. Fetch it with the URL and checksum in "
        "ser5/ansible/roles/voice/defaults/main.yml, or set VOICE_VAD_MODEL_PATH."
    )


class FakeStt:
    """Returns queued transcripts, or a numbered one, so turns are tellable apart."""

    def __init__(self) -> None:
        self.queue: list[str] = []
        self.n = 0

    async def transcribe(self, audio: bytes) -> str:
        self.n += 1
        return self.queue.pop(0) if self.queue else f"question number {self.n}"


class FakeLlm:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.seen: list[list[dict]] = []
        self._chunks = chunks or ["Two hundred", " sixty two thousand per slot."]

    async def stream(self, messages):
        self.seen.append(list(messages))
        for chunk in self._chunks:
            yield chunk


class FakeTts:
    async def stream(self, text: str):
        yield b"\x00\x01" * 16


class Unreachable:
    """A search/hermes stand-in that fails the test if the presence touches it."""

    def available(self) -> bool:
        return False

    async def search(self, query: str, **kwargs):
        raise AssertionError("the presence reached the network")

    async def aclose(self) -> None:
        pass


def build_app(db_path: str, vad_model: str, **overrides):
    """A FastAPI app wrapping a real Session with fake I/O. Returns (app, deps)."""
    cfg = replace(
        Config(), vad_model_path=vad_model, ledger_path=db_path, **overrides
    )
    ledger = Ledger(db_path)
    deps = Deps(
        stt=FakeStt(),
        llm=FakeLlm(),
        tts=FakeTts(),
        ledger=ledger,
        doorman=Doorman(ledger, history_turns=cfg.llm_history_turns),
        returner=Returner(ledger, quiet_s=cfg.return_quiet_s),
        hands=None,
        search=Unreachable(),
        hermes=Unreachable(),
        system_prompt="SYSTEM",
    )
    app = FastAPI()

    @app.websocket("/v1/stream")
    async def stream(ws: WebSocket) -> None:  # pragma: no cover - exercised via client
        await Session(ws, cfg, deps).run()

    @app.on_event("startup")
    async def _open() -> None:
        await ledger.open()

    @app.on_event("shutdown")
    async def _close() -> None:
        await ledger.aclose()

    return app, deps
