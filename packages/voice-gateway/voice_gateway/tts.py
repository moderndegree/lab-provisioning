"""Sentence chunking and streaming synthesis.

This module holds the single biggest latency lever in the whole design.

The naive voice loop waits for the model to finish, sends the finished text to a
synthesiser, waits for the audio, then plays it. That is additive: a 12-second
answer means 12 seconds of silence before the first word. Instead we cut the
token stream at sentence boundaries and synthesise each sentence as it lands, so
the user hears sentence one while sentence three is still being generated. The
perceived response time collapses to "time to first sentence", and everything
after that is hidden behind playback.

Synthesis targets any OpenAI-compatible /v1/audio/speech. `response_format:
"pcm"` returns raw s16le, streamed; every other format pays an encode on their
side and a decode on ours, for audio that is about to be played immediately.
Raw PCM goes socket to socket.

Engine chosen on measurement (ser5, 2026-08-30, time to FIRST BYTE, which is
what a listener experiences, not total synthesis time):

    speaches + piper en_US-amy-medium     250ms   RTF 0.047
    openedai-speech + piper (tts-1)      1028ms   RTF 0.349
    speaches + Kokoro-82M ONNX int8      5592ms   RTF 1.125  <- slower than
                                                                real time on CPU

Kokoro is the better-sounding model and is simply not viable here; it would need
a GPU. openedai-speech works but buffers through ffmpeg, and 780 ms of extra
time-to-first-byte is most of the latency budget for this whole project.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from .protocol import TTS_SAMPLE_RATE

log = logging.getLogger(__name__)

_TERMINALS = ".!?…"
_CLOSERS = "\"')]}»”’"
# First-chunk-only boundaries. Measured on ser5 2026-08-30: waiting for a full
# sentence cost 532 ms from endpoint to first speakable chunk while the model's
# warm TTFT was only 75 ms — i.e. almost all of it was tokens generated after
# the answer had already begun. Breaking the FIRST chunk at a clause boundary
# gets audio moving without changing anything after it.
_CLAUSES = ",;:—–"


class SentenceChunker:
    """Accumulates streamed text and emits speakable chunks.

    The first chunk is emitted on a much shorter minimum than the rest. That
    asymmetry is deliberate: the first flush is what the user experiences as
    response time, while later flushes happen behind audio that is already
    playing and can afford to wait for a cleaner boundary.
    """

    def __init__(
        self,
        *,
        first_min_chars: int,
        min_chars: int,
        max_chars: int,
        first_clause_break: bool = True,
    ) -> None:
        self._first_min = first_min_chars
        self._min = min_chars
        self._max = max_chars
        self._first_clause_break = first_clause_break
        self._buf = ""
        self._emitted = 0

    @property
    def min_chars(self) -> int:
        return self._first_min if self._emitted == 0 else self._min

    def feed(self, delta: str) -> list[str]:
        """Add streamed text; return any chunks that are ready to speak."""
        self._buf += delta
        out: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            out.append(chunk)
        return out

    def flush(self) -> str | None:
        """Emit whatever is left. Called once the model stream ends."""
        text = self._buf.strip()
        self._buf = ""
        if not text:
            return None
        self._emitted += 1
        return text

    # ─── internals ───────────────────────────────────────────────────────────
    def _take(self) -> str | None:
        cut = self._boundary()
        if cut is None:
            return None
        chunk = self._buf[:cut].strip()
        self._buf = self._buf[cut:].lstrip()
        if not chunk:
            return None
        self._emitted += 1
        return chunk

    def _boundary(self) -> int | None:
        buf = self._buf
        minimum = self.min_chars

        # A hard newline is always a boundary — models use it for lists, and a
        # list read as one run-on sentence is unlistenable.
        nl = buf.find("\n")
        if nl != -1 and len(buf[:nl].strip()) >= 1:
            return nl + 1

        # The first chunk may break at a clause boundary; later ones may not.
        # Only the first is on the critical path — after it, audio is already
        # playing and a cleaner boundary is worth more than a few hundred ms.
        breakers = _TERMINALS
        if self._emitted == 0 and self._first_clause_break:
            breakers += _CLAUSES

        if len(buf) >= minimum:
            for i, ch in enumerate(buf):
                if ch not in breakers:
                    continue
                end = i + 1
                # Absorb trailing quotes/brackets so we do not strand them at
                # the head of the next chunk.
                while end < len(buf) and buf[end] in _CLOSERS:
                    end += 1
                if end < len(buf) and not buf[end].isspace():
                    continue  # "3.5" or "e.g." mid-token — not a boundary
                if len(buf[:end].strip()) < minimum:
                    continue
                if ch == "." and self._looks_like_abbreviation(buf, i):
                    continue
                return end

        # Runaway guard: a model that never emits terminal punctuation still has
        # to produce audio. Break at the last space so we do not cut a word.
        if len(buf) >= self._max:
            space = buf.rfind(" ", 0, self._max)
            return space + 1 if space > 0 else self._max
        return None

    @staticmethod
    def _looks_like_abbreviation(buf: str, dot: int) -> bool:
        """Cheap guard against 'Dr.', 'e.g.', 'U.S.' becoming sentence ends.

        Only two shapes, both common in spoken answers: a single letter before
        the dot (initials, e.g./i.e. after their first dot), and a short
        capitalised token from a small fixed list.
        """
        if dot == 0:
            return False
        if dot == 1 or not buf[dot - 2].isalpha():
            # single letter preceded by a non-letter -> "J." / "e." / "g."
            return buf[dot - 1].isalpha()
        start = dot
        while start > 0 and buf[start - 1].isalpha():
            start -= 1
        return buf[start:dot].lower() in {
            "mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "approx", "fig", "no",
        }


class TtsClient:
    """Streaming synthesis against an OpenAI-compatible /v1/audio/speech."""

    sample_rate = TTS_SAMPLE_RATE

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        voice: str,
        speed: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._voice = voice
        self._speed = speed
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield raw int16 PCM at `sample_rate` as it is produced."""
        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": "pcm",
            "speed": self._speed,
        }
        async with self._client.stream(
            "POST", f"{self._base_url}/v1/audio/speech", json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:400]
                raise RuntimeError(f"TTS {resp.status_code}: {body}")
            async for block in resp.aiter_bytes():
                if block:
                    yield block

    async def health(self) -> bool:
        """Synthesise a fixed short string. Deliberately a real synthesis and
        not a GET on the root: openedai-speech answers HTTP long before piper
        has a usable voice on disk, and the difference only shows up when
        something asks it to actually speak."""
        try:
            async for _ in self.stream("ok"):
                return True
        except Exception:  # noqa: BLE001 - health must not raise
            return False
        return False
