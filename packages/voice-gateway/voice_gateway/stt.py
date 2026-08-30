"""Speech to text against an OpenAI-compatible /v1/audio/transcriptions.

WHY BATCH AND NOT STREAMING — this reverses the original design, on measurements
taken on ser5 2026-08-29 against a 3.88 s clip, 8 CPU threads, int8:

    path                                tiny.en   base.en   small.en
    faster-whisper in-process             237ms     408ms     1260ms
    speaches (HTTP container)             460ms     708ms     1876ms
    WhisperLive REST (--enable_rest)     3060ms    3060ms     3100ms
    WhisperLive streaming, tail lag      2300ms    2750ms     2200ms

WhisperLive was chosen originally because streaming partials should make the
finalize pass cheap. In practice its own server refuses to transcribe a chunk
shorter than one second — `backend/base.py`:

    if duration < 1.0:
        time.sleep(0.1)     # wait for audio chunks to arrive

so the tail of an utterance waits out that accumulation window plus a whisper
pass. The lag is IDENTICAL across tiny/base/small, which proves it is the
cadence and not the compute; no model choice or flag reaches it. Its REST path
is worse still, and flat across model sizes for the same reason.

There is a second, unrelated trap in it worth recording since it cost real time:
`END_OF_AUDIO` must be a BINARY frame. As text the server raises "a bytes-like
object is required, not 'str'", kills the transcription thread and hangs up —
returning an EMPTY transcript with no error to the client.

So: buffer the utterance, send it once at the endpoint. The endpoint is free in
push-to-talk mode (key release) and costs the VAD hangover otherwise, and a
single batch pass on ser5's CPU beats streaming by 3-4x here.
"""

from __future__ import annotations

import io
import logging
import wave

import httpx

from .protocol import INPUT_SAMPLE_RATE

log = logging.getLogger(__name__)


class SttError(RuntimeError):
    pass


def pcm_to_wav(pcm: bytes, sample_rate: int = INPUT_SAMPLE_RATE) -> bytes:
    """Wrap raw int16 mono PCM in a WAV container, in memory.

    The endpoint takes a file upload and sniffs the format; handing it bare PCM
    means it has to guess, and it guesses wrong. Building the 44-byte header
    costs nothing measurable next to the transcription itself.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class SttClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        language: str,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._language = language
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(self, pcm: bytes) -> str:
        """Transcribe one buffered utterance."""
        if len(pcm) < INPUT_SAMPLE_RATE // 10 * 2:  # under ~100 ms
            return ""
        data = {"model": self._model, "response_format": "json"}
        # Pinning the language skips whisper's detection pass. It is a real
        # saving on short clips, where detection is a meaningful fraction of the
        # total, and this lab is English-only.
        if self._language:
            data["language"] = self._language

        resp = await self._client.post(
            f"{self._base_url}/v1/audio/transcriptions",
            files={"file": ("utterance.wav", pcm_to_wav(pcm), "audio/wav")},
            data=data,
        )
        if resp.status_code != 200:
            raise SttError(f"STT {resp.status_code}: {resp.text[:300]}")
        return (resp.json().get("text") or "").strip()

    async def health(self) -> tuple[bool, str]:
        """Transcribe 300 ms of silence.

        Deliberately a real transcription rather than a GET on /health: speaches
        answers HTTP long before a model is downloaded into its cache, and a
        missing model surfaces only when something asks it to transcribe. That
        is the same class of failure the Open WebUI role documents — the service
        looks perfectly healthy and simply cannot do its job.
        """
        try:
            await self.transcribe(b"\x00" * (INPUT_SAMPLE_RATE // 2))
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return False, str(exc)[:200]
        return True, f"{self._base_url} ({self._model})"
