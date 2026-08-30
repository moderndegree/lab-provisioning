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

Conversation is NOT held here. Every turn is written through to the Ledger and
the history this session reasons over is read back from it on connect, so a
dropped socket is no longer amnesia and the phone and the workstation are the
same assistant. What lives in this object is only what dies with the socket
anyway: VAD state, the utterance buffer, the task currently speaking.

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
from .doorman import DEFAULT_SPEAKER, Admission, Doorman
from .hands import Hands
from .ledger import Brief, Ledger
from .llm import LlmClient, Message
from .delivery import Returner
from .judgment import Verdict, amendment, judge
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
    ledger: Ledger
    doorman: Doorman
    returner: Returner
    hands: Hands | None
    search: WebSearch
    hermes: HermesDelegate
    system_prompt: str


class Session:
    def __init__(self, ws: WebSocket, cfg: Config, deps: Deps) -> None:
        self._ws = ws
        self._cfg = cfg
        self._deps = deps

        self.device = "unknown"
        self.speaker = DEFAULT_SPEAKER
        self.mode: p.Mode = "ptt"
        # Assigned by the Doorman at handshake. Empty means the Ledger was
        # unreachable and this turn will not be durable — see _handshake.
        self.conversation_id = ""

        self._state = State.IDLE
        # A working copy of the tail of the conversation, seeded from the Ledger
        # on connect. The Ledger is authoritative; this exists so the prompt can
        # be assembled without a read on the fast path.
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
        # Briefs this conversation has in flight, newest last. Held in memory so
        # that spotting an amendment costs no disk read on the fast path; the
        # Ledger stays authoritative and this is re-seeded from it on connect.
        self._live: list[Brief] = []
        # When the presence last did anything. A seam is measured from here, so
        # a result never lands on the tail of the sentence you just heard.
        self._quiet_since = time.monotonic()
        # Bumped by every _reset. A turn captures it on entry and its cleanup
        # only fires if it still matches — see _respond.
        self._generation = 0

    # ─── entry point ─────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self._ws.accept()
        try:
            admission = await self._handshake()
            await self._ws.send_json(
                p.ready(
                    admission.conversation_id,
                    resumed=len(admission.history),
                    working=len(admission.live_briefs),
                    waiting=len(admission.waiting),
                )
            )
            # Return runs for as long as the socket does. Without it a result
            # only ever arrives when the user happens to speak again, which is
            # not "results come back" — it is the human doing the polling.
            self._spawn(self._return_loop())
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

    async def _handshake(self) -> Admission:
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

        admission = await self._deps.doorman.admit(
            device=self.device, speaker=hello.get("speaker")
        )
        self.speaker = admission.speaker
        self.conversation_id = admission.conversation_id
        self._history = [
            {"role": t.role, "content": t.text} for t in admission.history
        ]
        self._live = list(admission.live_briefs)
        log.info(
            "session up: device=%s speaker=%s mode=%s conversation=%s resumed=%d",
            self.device,
            self.speaker,
            self.mode,
            self.conversation_id,
            len(self._history),
        )
        return admission

    # ─── inbound ─────────────────────────────────────────────────────────────
    async def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == p.C_START:
            # A turn is beginning, and it brings its own endpointing mode. This
            # is what lets ONE socket serve both a push-to-talk hold and a
            # wake-word turn — which in turn is what lets the presence be a
            # single floor rather than a new connection per utterance. A second
            # socket that can also speak is a second voice.
            requested = msg.get("mode")
            if requested in ("ptt", "vad"):
                self.mode = requested
            if self._state in (State.THINKING, State.SPEAKING):
                # Talking over the assistant by pressing the key IS barge-in.
                await self._cancel()
            self._reset()
            self._quiet_since = time.monotonic()
            # THE EARLIEST POSSIBLE MOMENT. `start` is sent at key-press, before
            # the user has finished the sentence, so this is a second or two of
            # warning for the bench to abandon its generation before the presence
            # needs the GPU at all. Everything after this is catch-up.
            await self._take_floor()
            return
        if kind == p.C_END:
            if self._state is State.LISTENING:
                await self._endpoint()
        elif kind == p.C_CANCEL:
            await self._cancel()

    async def _on_audio(self, frame: bytes) -> None:
        self._quiet_since = time.monotonic()
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
        # Also here, for a client that never sends `start` — the bench harness,
        # and any older client. Later than the `start` frame, but still ahead of
        # STT and well ahead of the first token.
        await self._take_floor()
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
        await self._take_floor()
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
        # The generation bump is what makes _reset safe to call from a turn that
        # has already handed the floor back. See _respond's finally.
        self._generation += 1
        # The seam is measured from HERE — the moment the floor came free — not
        # from the last audio frame. On a persistent socket the mic stops between
        # turns, so measuring from the last frame would make a gap look older
        # than it is and let a result land on the tail of an answer.
        self._quiet_since = time.monotonic()
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
        # THIS TURN'S CLAIM ON THE STATE MACHINE. Everything after `done` — the
        # ledger write, the vault export — awaits, and while it awaits the NEXT
        # utterance can legitimately arrive and put the session into LISTENING.
        # A blind `finally: self._reset()` then wipes a turn that had barely
        # started: its buffered audio is dropped, its `end` finds a session that
        # is not LISTENING and is ignored, and the turn disappears with no error
        # anywhere. Reproduced 5/15 runs; the symptom is "sometimes it just
        # doesn't hear me", which is the worst kind of bug this system can have.
        #
        # _reset bumps the generation, so once ANY reset has happened — this
        # turn's own, or a newer turn's — the cleanup below correctly declines
        # to run.
        generation = self._generation
        try:
            # Hands first, because consent is the most time-critical reading
            # of an utterance: "yes" said right after a confirmation question
            # means one thing and nothing else, and any other interpretation of
            # it would leave an irreversible action hanging on a maybe.
            if self._deps.hands is not None:
                outcome, action, spoken = self._deps.hands.resolve(
                    utterance, time.monotonic()
                )
                if outcome in ("confirm", "cancelled"):
                    await self._say(utterance, spoken)
                    return
                if outcome == "run" and action is not None:
                    await self._act(utterance, action)
                    return

            # Steering is checked BEFORE classification, and only when
            # something is actually in flight. "Actually, make it vulkan only"
            # is a correction to a running errand; the same words with nothing
            # running are just a sentence.
            if self._live:
                correction = amendment(utterance)
                if correction:
                    await self._amend(correction)
                    return
            verdict = judge(utterance)
            if verdict.kind == "errand":
                await self._errand(verdict, utterance)
                return
            await self._answer(utterance, utterance, started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad turn, not one dead session
            log.exception("turn failed")
            await self._fail(f"Something went wrong: {exc}")
        finally:
            if self._generation == generation and self._state is not State.IDLE:
                self._reset()

    async def _answer(self, utterance: str, prompt: str, started: float) -> None:
        """Speak an answer from the presence's own model. The fast path.

        Everything it awaits is on lab hardware: mini for the tokens, ser5's
        loopback for the audio. No branch of this method may grow a call to
        anything else — if the fast path can reach the internet then the fast
        path's latency IS the internet's latency, and the response guarantee this
        whole design exists to make is gone.
        """
        try:
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
            latency_ms = (
                int((first_audio_at - started) * 1000)
                if first_audio_at is not None
                else None
            )
            if first_audio_at is not None:
                log.info(
                    "turn %s: first audio %.0f ms, total %.0f ms",
                    self.device,
                    (first_audio_at - started) * 1000,
                    (time.perf_counter() - started) * 1000,
                )
            # ORDER HERE IS LOAD-BEARING. `done` tells the client the floor is
            # free, and a person hears it as permission to speak — in ptt mode
            # the key can be down again a few milliseconds later. Anything still
            # SPEAKING when that frame lands routes it to the barge-in check,
            # which sees a syllable of a fresh utterance, does not call it
            # speech, and DROPS it; the `end` that follows then finds a session
            # that is not LISTENING and is ignored entirely. The turn vanishes
            # with no error anywhere.
            #
            # So: IDLE first, `done` second, and the durable write last, where
            # nobody is waiting on it. Reproduced 3/5 runs before this order was
            # fixed, 0/20 after.
            self._reset()
            await self._send_safe(p.done())
            if answer:
                await self._remember(utterance, answer, latency_ms=latency_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad turn, not one dead session
            log.exception("turn failed")
            await self._fail(f"Something went wrong: {exc}")

    async def _speak(self, seq: int, text: str) -> None:
        self._state = State.SPEAKING
        await self._ws.send_json(p.speaking(seq, text))
        async for block in self._deps.tts.stream(text):
            await self._ws.send_bytes(block)
        # Refreshed AFTER the audio, never before: this is a disk write, and the
        # first sentence's bytes are the number this whole design minimises. A
        # long answer keeps pushing the deadline forward so the bench does not
        # wake up in the middle of it.
        await self._take_floor()

    async def _take_floor(self) -> None:
        """Tell the bench to keep its hands off the GPU for a few seconds.

        Never raises and never blocks the turn. If the Ledger is unavailable the
        presence simply gets no protection, which is exactly the behaviour it had
        before this existed — degraded, not broken.
        """
        if not self.conversation_id:
            return
        try:
            await self._deps.ledger.take_floor(self.device, self._cfg.floor_hold_s)
        except Exception:  # noqa: BLE001
            log.debug("could not take the floor", exc_info=True)

    # ─── errands ─────────────────────────────────────────────────────────────
    async def _errand(self, verdict: Verdict, utterance: str) -> None:
        """A turn the presence should not try to answer itself.

        The bench does not exist yet, so there are two branches and neither is
        the final shape. `presence_network` picks between them: on, and the
        presence does the work inline exactly as it always has; off, and it
        refuses out loud. The second is what this phase is actually claiming —
        one room, one voice, no network at all — and the first is only there so
        that a working feature does not go dark while the bench is being built.
        Both die when the bench lands and dispatch takes their place.
        """
        if self._cfg.bench_enabled and self.conversation_id:
            await self._dispatch(verdict)
            return
        if self._cfg.presence_network:
            await self._presence_does_it_itself(verdict, utterance)
            return
        await self._refuse(verdict)

    async def _dispatch(self, verdict: Verdict) -> None:
        """Open a brief and hand the floor straight back.

        THE LEDGER WRITE IS THE HANDOFF. There is no in-process queue between the
        presence and the bench, which is why a gateway restart between "on it"
        and the bench claiming the row loses nothing — and why the presence can
        say "on it" honestly rather than hopefully.
        """
        brief_id = await self._deps.ledger.open_brief(
            conversation_id=self.conversation_id,
            statement=verdict.text,
            speaker=self.speaker,
            device=self.device,
            capability=verdict.capability,
        )
        brief = await self._deps.ledger.get_brief(brief_id)
        if brief is not None:
            self._live.append(brief)
        await self._send_safe(p.brief(brief_id, verdict.text))
        await self._send_safe(p.working(len(self._live)))
        spoken = "On it. I'll come back to you."
        await self._speak(1, spoken)
        self._reset()
        await self._send_safe(p.done())
        await self._remember(verdict.text, spoken, brief_id=brief_id)

    async def _amend(self, correction: str) -> None:
        """Steer the newest brief in flight without stopping the conversation.

        Appended rather than substituted: the half of the request that did not
        change is still the request. The bench notices the revision moved and
        abandons whatever it was generating, which costs it the tokens it had
        already produced — the strategy accepts exactly that, deep work slower
        than its peak because steerability requires yielding.
        """
        brief = self._live[-1]
        statement = f"{brief.statement}. {correction[0].upper()}{correction[1:]}."
        try:
            await self._deps.ledger.amend_brief(
                brief.id, statement, note=f"spoken on {self.device}"
            )
        except KeyError:
            # The brief finished between the utterance and this write. Treat the
            # correction as a fresh request rather than dropping it silently.
            self._live.remove(brief)
            await self._answer(correction, correction, time.perf_counter())
            return
        updated = await self._deps.ledger.get_brief(brief.id)
        if updated is not None:
            self._live[-1] = updated
        spoken = "Got it, I'll factor that in."
        await self._speak(1, spoken)
        self._reset()
        await self._send_safe(p.done())
        await self._remember(correction, spoken, brief_id=brief.id)

    # ─── hands ───────────────────────────────────────────────────────────────
    async def _act(self, utterance: str, action) -> None:
        """Run a registered action and say what happened.

        Consent, if it was needed, has already been given — Hands.resolve only
        returns "run" for a reversible action or for a clean spoken yes to an
        irreversible one. This method does not re-check, and must not: a second
        gate here would mean the first one was not the gate.
        """
        log.info("hands: running %s for %s", action.name, self.device)
        try:
            output = await action.run()
        except Exception as exc:  # noqa: BLE001 - a failed action is a sentence
            log.exception("hands: %s failed", action.name)
            await self._say(utterance, f"That didn't work: {exc}")
            return
        # Spoken, so it is trimmed to something a person can listen to. The full
        # output goes to the client as a notice for anything with a screen.
        await self._send_safe(p.notice(output[:2000]))
        first = output.strip().splitlines()[0] if output.strip() else "Done."
        await self._say(utterance, first[:300])

    async def _say(self, utterance: str, spoken: str) -> None:
        """One sentence from the presence, recorded like any other turn.

        IDLE before `done`, for the reason spelled out in `_answer`: the client
        may start the next utterance the instant it hears the turn is over.
        """
        await self._speak(1, spoken)
        self._reset()
        await self._send_safe(p.done())
        await self._remember(utterance, spoken)

    async def _refuse(self, verdict: Verdict) -> None:
        """Say no, out loud, in the presence's own voice.

        A denial is re-voiced, never surfaced. Hermes refusing, or a missing
        bench, is not a conversation; the presence saying "I can't do that one"
        is. Which is also why this speaks rather than sending an `error` frame —
        an error is something a client renders, and there is no screen here.
        """
        if verdict.capability == "delegation":
            spoken = "I can't hand work off yet. Ask me again once the bench is running."
        else:
            spoken = "That one needs the web, and I can't reach it from here."
        await self._say(verdict.text, spoken)

    # ─── transitional: what the bench will take over ─────────────────────────
    async def _presence_does_it_itself(self, verdict: Verdict, utterance: str) -> None:
        if verdict.capability == "delegation":
            await self._delegate(verdict.text)
            return
        # Retrieval, folded into the SAME model call that answers rather than
        # costing a second pass. It also costs ~1.6s of SearXNG round trip
        # sitting directly on the fast path, which is the reason this belongs to
        # the bench and not here.
        started = time.perf_counter()
        try:
            results = await self._deps.search.search(verdict.text)
            prompt = (
                f"{WebSearch.as_context(verdict.text, results)}\n\n"
                f"Using those results, answer out loud and briefly: {verdict.text}"
            )
        except Exception as exc:  # noqa: BLE001 - degrade, do not die
            log.warning("search failed: %s", exc)
            prompt = (
                f"{utterance}\n\n(Web search is unavailable; say so if the "
                f"answer needs current information.)"
            )
        await self._answer(utterance, prompt, started)

    async def _delegate(self, task_text: str) -> None:
        if not self._deps.hermes.available():
            await self._fail("Hermes is not on my path, so I cannot delegate that.")
            return
        await self._speak(1, "On it. I'll tell you when Hermes is done.")
        self._reset()
        await self._send_safe(p.done())

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
            self._reset()
            await self._ws.send_json(p.done())
        except Exception:  # noqa: BLE001 - client left; nothing to do
            log.info("delegation finished after client left: %s", result[:200])
        finally:
            self._reset()

    # ─── Return ──────────────────────────────────────────────────────────────
    async def _return_loop(self) -> None:
        """Wait for a seam, then speak one finished result. Forever.

        Polls rather than being pushed because the bench is a separate process
        and the Ledger is the only thing between them. A poll every few seconds
        against a WAL-mode SQLite file is a rounding error next to a single
        conversational turn, and it means a bench crash, a gateway restart or a
        reconnect from another device all recover on their own.
        """
        if not self.conversation_id:
            return
        while True:
            await asyncio.sleep(self._cfg.return_poll_s)
            try:
                # `_response is None` would be wrong: it holds the last turn's
                # task, COMPLETED, from the moment the first turn ends and is
                # only cleared by a cancel. Testing it for None meant the seam
                # never opened again after a single turn, and a finished errand
                # waited forever with the floor sitting empty in front of it.
                busy = self._response is not None and not self._response.done()
                if not self._deps.returner.seam(
                    idle=self._state is State.IDLE and not busy,
                    quiet_for=time.monotonic() - self._quiet_since,
                ):
                    continue
                due = await self._deps.returner.next_due(self.conversation_id)
                if due is None:
                    continue
                # Delivery runs AS the response task so that barge-in cancels it
                # like anything else. Someone who starts talking over a returning
                # result is interrupting the presence, and the presence stops.
                self._response = asyncio.create_task(self._deliver(*due))
                await self._response
                self._response = None
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad delivery is not a dead loop
                log.exception("return loop")

    async def _deliver(self, result, brief) -> None:
        generation = self._generation
        spoken = self._deps.returner.phrase(result, brief)
        log.info("returning brief %s to %s after %.0fs",
                 brief.id, self.device, time.time() - brief.created_ts)
        try:
            await self._send_safe(p.notice(result.text[:2000]))
            await self._speak(1, spoken)
            self._reset()
            await self._send_safe(p.done())
        except asyncio.CancelledError:
            # Barged in on. The result stays undelivered on purpose — it will be
            # offered again at the next seam rather than being lost to the
            # interruption that cut it off.
            raise
        else:
            await self._deps.returner.delivered(result.id)
            self._live = [b for b in self._live if b.id != brief.id]
            await self._send_safe(p.working(len(self._live)))
            await self._remember("", spoken, brief_id=brief.id)
        finally:
            self._quiet_since = time.monotonic()
            # Same guard as _respond: the listener may already have started the
            # next turn while this result was being spoken.
            if self._generation == generation:
                self._reset()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

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

    async def _remember(
        self,
        user: str,
        assistant: str,
        *,
        latency_ms: int | None = None,
        brief_id: str | None = None,
    ) -> None:
        """Commit one exchange to the working history AND to the Ledger.

        An exchange cancelled by barge-in is deliberately NOT recorded. The model
        did not finish the thought and the listener cut it off precisely because
        it was going wrong; keeping it would poison the next turn's context with
        the thing the user just rejected.
        """
        if user:
            self._history.append({"role": "user", "content": user})
        self._history.append({"role": "assistant", "content": assistant})
        keep = self._cfg.llm_history_turns * 2
        if len(self._history) > keep:
            del self._history[: len(self._history) - keep]

        if not self.conversation_id:
            return
        try:
            # ONE call, one transaction. An empty `user` is a result returning
            # with no question in front of it, and the Ledger skips that half.
            await self._deps.ledger.record_exchange(
                conversation_id=self.conversation_id,
                speaker=self.speaker,
                device=self.device,
                user=user,
                assistant=assistant,
                latency_ms=latency_ms,
                brief_id=brief_id,
            )
        except Exception:  # noqa: BLE001 - a lost row must not lose the session
            log.exception("ledger write failed; this exchange is not durable")

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
            # GRACE BEFORE THE AXE. `done` is sent to the client before the
            # Ledger write, so a turn can be mid-INSERT at the moment the socket
            # closes — and a client that hangs up the instant it hears the answer
            # is completely ordinary. Cancelling immediately threw that exchange
            # away: the user heard it, the Ledger never did, and the next session
            # resumed a conversation missing its last turn.
            #
            # Short, because the other thing this cancels is a response still
            # generating audio for a client that has gone, and nothing is served
            # by waiting for that.
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(
                    asyncio.shield(self._response), timeout=self._cfg.teardown_grace_s
                )
        if self._response and not self._response.done():
            self._response.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._response
        # AWAITED, not just cancelled. `task.cancel()` only schedules the
        # CancelledError; the coroutine has not stopped until the loop has run it
        # again. Returning from here with pending tasks left the return loop
        # holding a Ledger executor future across the loop's own shutdown, which
        # hung the process on exit roughly 60% of the time — reproduced 5/5 runs
        # at ~20s each before this await was added, 0/5 after.
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("session down: device=%s", self.device)
