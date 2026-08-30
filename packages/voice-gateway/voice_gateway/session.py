"""One connected client: the state machine that turns audio into audio.

    IDLE ──speech / first ptt frame──► LISTENING ──endpoint──► THINKING
      ▲                                                            │
      │                                                     first chunk
      └────────────── done / cancelled ◄── SPEAKING ◄──────────────┘

Two things in here are the difference between "a voice demo" and "something you
would actually use":

  sentence-chunked speech
      The model stream is cut at sentence boundaries and each sentence is
      synthesised and shipped as it lands, so the user hears sentence one while
      sentence three is still being generated (see tts.SentenceChunker).

  barge-in
      While SPEAKING we keep running VAD on the inbound stream. Real speech
      cancels the in-flight model response and the queued audio immediately.
      Without this the assistant talks over you and you have to wait it out,
      which is the single most common reason a voice loop gets abandoned.

A PRE-ROLL buffer matters more than it looks. In vad mode we only know speech
started after `min_speech_ms` of it, so the frames that carry the beginning of
the first word have already gone by. They are kept in a small ring and become
the head of the utterance buffer; without that every utterance loses its first
syllable, which reads as a bad ASR model rather than as a bad buffer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

from fastapi import WebSocket, WebSocketDisconnect

from . import protocol as p
from .config import Config
from .llm import LlmClient, Message
from .router import route
from .stt import SttClient, SttError
from .tools import HermesDelegate, WebSearch
from .tts import SentenceChunker, TtsClient
from .vad import Endpointer, SileroVad

log = logging.getLogger(__name__)

# 300 ms of lead-in. Enough to cover min_speech_ms plus the VAD's own 32 ms
# window granularity, and small enough that it never contains a previous turn.
_PREROLL_FRAMES = 15


class State(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()


@dataclass
class Deps:
    stt: SttClient
    llm: LlmClient
    tts: TtsClient
    search: WebSearch
    hermes: HermesDelegate
    system_prompt: str


class Session:
    def __init__(self, ws: WebSocket, cfg: Config, deps: Deps) -> None:
        self._ws = ws
        self._cfg = cfg
        self._deps = deps

        self.device = "unknown"
        self.mode: p.Mode = "ptt"

        self._state = State.IDLE
        self._history: list[Message] = []
        self._preroll: deque[bytes] = deque(maxlen=_PREROLL_FRAMES)

        # The whole utterance is buffered and transcribed in one pass at the
        # endpoint. At 16 kHz mono int16 the max-length utterance is under a
        # megabyte, so this is cheap; see stt.py for why batch beats streaming
        # on this hardware.
        self._utterance = bytearray()
        self._utterance_ms = 0

        self._endpointer = Endpointer(
            SileroVad(cfg.vad_model_path, cfg.vad_threshold),
            frame_ms=p.FRAME_MS,
            min_speech_ms=cfg.vad_min_speech_ms,
            silence_ms=cfg.vad_silence_ms,
        )
        # A second, independent detector. It must not share LSTM state with the
        # endpointer: they are asked different questions at different times, and
        # a shared state means the tail of the user's own last utterance biases
        # the barge-in decision on the assistant's reply.
        self._barge_vad = SileroVad(cfg.vad_model_path, cfg.vad_threshold)
        self._barge_ms = 0

        self._response: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()

    # ─── entry point ─────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self._ws.accept()
        try:
            await self._handshake()
            await self._ws.send_json(p.ready())
            while True:
                msg = await self._ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self._on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    await self._on_text(msg["text"])
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - one bad session must not kill the server
            log.exception("session %s failed", self.device)
        finally:
            await self._teardown()

    async def _handshake(self) -> None:
        raw = await self._ws.receive_text()
        hello = json.loads(raw)
        if hello.get("type") != p.C_HELLO:
            raise ValueError(f"expected hello, got {hello.get('type')!r}")
        self.device = str(hello.get("device") or "unknown")
        self.mode = "vad" if hello.get("mode") == "vad" else "ptt"
        rate = int(hello.get("sample_rate") or p.INPUT_SAMPLE_RATE)
        if rate != p.INPUT_SAMPLE_RATE:
            # Resampling here would be pure added latency on every frame, and
            # every client we ship can open its device at 16 kHz.
            raise ValueError(f"sample_rate must be {p.INPUT_SAMPLE_RATE}, got {rate}")
        log.info("session up: device=%s mode=%s", self.device, self.mode)

    # ─── inbound ─────────────────────────────────────────────────────────────
    async def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == p.C_END:
            if self._state is State.LISTENING:
                await self._endpoint()
        elif kind == p.C_CANCEL:
            await self._cancel()

    async def _on_audio(self, frame: bytes) -> None:
        if self._state in (State.THINKING, State.SPEAKING):
            await self._check_barge_in(frame)
            return

        self._preroll.append(frame)

        if self._state is State.IDLE:
            if self.mode == "ptt":
                # The client only transmits while the key is held, so the first
                # frame IS the start of the utterance. No VAD, no hangover —
                # this is why ptt mode is the fast path.
                await self._begin_utterance()
            else:
                if self._endpointer.feed(frame) or self._endpointer.started:
                    await self._begin_utterance()
            return

        # LISTENING
        await self._forward(frame)
        self._utterance_ms += p.FRAME_MS

        if self._utterance_ms >= self._cfg.max_utterance_ms:
            log.warning("utterance hit max length; endpointing")
            await self._endpoint()
            return

        if self.mode == "vad" and self._endpointer.feed(frame):
            await self._endpoint()

    async def _check_barge_in(self, frame: bytes) -> None:
        if self._barge_vad.is_speech(frame):
            self._barge_ms += p.FRAME_MS
            if self._barge_ms >= self._cfg.barge_in_ms:
                log.info("barge-in on %s", self.device)
                await self._cancel()
        else:
            self._barge_ms = 0

    # ─── utterance lifecycle ─────────────────────────────────────────────────
    async def _begin_utterance(self) -> None:
        self._state = State.LISTENING
        self._utterance_ms = 0
        # Start from the pre-roll so the first syllable survives. In vad mode we
        # only learn speech began after min_speech_ms of it, by which point the
        # frames carrying the start of the first word have already arrived.
        self._utterance = bytearray(b"".join(self._preroll))
        self._preroll.clear()

    async def _forward(self, frame: bytes) -> None:
        self._utterance += frame

    async def _endpoint(self) -> None:
        self._state = State.THINKING
        self._endpointer.reset()
        self._preroll.clear()

        audio, self._utterance = bytes(self._utterance), bytearray()
        try:
            transcript = await self._deps.stt.transcribe(audio)
        except (SttError, OSError) as exc:
            log.error("STT failed: %s", exc)
            await self._fail("Speech recognition is unavailable.")
            return

        transcript = transcript.strip()
        if not transcript:
            # Silence, a cough, or the mic picking up the tail of our own audio.
            # Say nothing; going back to IDLE quietly is the right behaviour.
            self._state = State.IDLE
            await self._send_safe(p.done())
            return

        await self._send_safe(p.final(transcript))
        self._barge_ms = 0
        self._barge_vad.reset()
        self._response = asyncio.create_task(self._respond(transcript))

    async def _cancel(self) -> None:
        if self._response and not self._response.done():
            self._response.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._response
        self._response = None
        self._reset()
        await self._send_safe(p.cancelled())

    def _reset(self) -> None:
        self._state = State.IDLE
        self._endpointer.reset()
        self._barge_vad.reset()
        self._barge_ms = 0
        self._utterance_ms = 0
        self._utterance = bytearray()
        self._preroll.clear()

    # ─── responding ──────────────────────────────────────────────────────────
    async def _respond(self, utterance: str) -> None:
        started = time.perf_counter()
        try:
            decision = route(utterance)

            if decision.kind == "delegate":
                await self._delegate(decision.text)
                return

            prompt = utterance
            if decision.kind == "search":
                try:
                    results = await self._deps.search.search(decision.text)
                    prompt = (
                        f"{WebSearch.as_context(decision.text, results)}\n\n"
                        f"Using those results, answer out loud and briefly: {decision.text}"
                    )
                except Exception as exc:  # noqa: BLE001 - degrade, do not die
                    log.warning("search failed: %s", exc)
                    prompt = (
                        f"{utterance}\n\n(Web search is unavailable; say so if the "
                        f"answer needs current information.)"
                    )

            messages = self._messages(prompt)
            chunker = SentenceChunker(
                first_min_chars=self._cfg.first_chunk_min_chars,
                min_chars=self._cfg.chunk_min_chars,
                max_chars=self._cfg.chunk_max_chars,
                first_clause_break=self._cfg.first_chunk_clause_break,
            )
            spoken: list[str] = []
            seq = 0
            first_audio_at: float | None = None

            async for delta in self._deps.llm.stream(messages):
                for chunk in chunker.feed(delta):
                    seq += 1
                    await self._speak(seq, chunk)
                    spoken.append(chunk)
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()

            tail = chunker.flush()
            if tail:
                seq += 1
                await self._speak(seq, tail)
                spoken.append(tail)
                if first_audio_at is None:
                    first_audio_at = time.perf_counter()

            answer = " ".join(spoken).strip()
            if answer:
                self._remember(utterance, answer)

            if first_audio_at is not None:
                log.info(
                    "turn %s: first audio %.0f ms, total %.0f ms",
                    self.device,
                    (first_audio_at - started) * 1000,
                    (time.perf_counter() - started) * 1000,
                )
            await self._send_safe(p.done())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad turn, not one dead session
            log.exception("turn failed")
            await self._fail(f"Something went wrong: {exc}")
        finally:
            if self._state is not State.IDLE:
                self._reset()

    async def _speak(self, seq: int, text: str) -> None:
        self._state = State.SPEAKING
        await self._ws.send_json(p.speaking(seq, text))
        async for block in self._deps.tts.stream(text):
            await self._ws.send_bytes(block)

    async def _delegate(self, task_text: str) -> None:
        if not self._deps.hermes.available():
            await self._fail("Hermes is not on my path, so I cannot delegate that.")
            return
        await self._speak(1, "On it. I'll tell you when Hermes is done.")
        await self._send_safe(p.done())
        self._reset()

        task = asyncio.create_task(self._await_delegation(task_text))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _await_delegation(self, task_text: str) -> None:
        result = await self._deps.hermes.run(task_text)
        # Deliberately no store-and-forward. If the client has gone, the answer
        # is logged and dropped: speaking the result of a question asked an hour
        # ago, out of nowhere, is worse than not answering.
        summary = result.strip().splitlines()
        spoken = summary[0][:400] if summary else "Hermes finished."
        try:
            await self._ws.send_json(p.notice(result[:2000]))
            await self._speak(1, f"Hermes is done. {spoken}")
            await self._ws.send_json(p.done())
        except Exception:  # noqa: BLE001 - client left; nothing to do
            log.info("delegation finished after client left: %s", result[:200])
        finally:
            self._reset()

    # ─── conversation state ──────────────────────────────────────────────────
    def _messages(self, prompt: str) -> list[Message]:
        # The system prompt is first and byte-identical every turn. That is what
        # makes it a prefix-cache hit on mini, which the llamacpp defaults call
        # the highest-value untuned knob on the box (~12x on TTFT).
        return [
            {"role": "system", "content": self._deps.system_prompt},
            *self._history,
            {"role": "user", "content": prompt},
        ]

    def _remember(self, user: str, assistant: str) -> None:
        self._history.append({"role": "user", "content": user})
        self._history.append({"role": "assistant", "content": assistant})
        keep = self._cfg.llm_history_turns * 2
        if len(self._history) > keep:
            del self._history[: len(self._history) - keep]

    # ─── plumbing ────────────────────────────────────────────────────────────
    async def _send_safe(self, payload: dict) -> None:
        try:
            await self._ws.send_json(payload)
        except Exception:  # noqa: BLE001 - client may have gone mid-turn
            pass

    async def _fail(self, message: str) -> None:
        await self._send_safe(p.error(message))
        self._reset()

    async def _teardown(self) -> None:
        if self._response and not self._response.done():
            self._response.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._response
        for task in list(self._background):
            task.cancel()
        log.info("session down: device=%s", self.device)
