"""OpenAI-compatible /v1/audio/speech in front of Chatterbox Turbo.

The gateway already speaks that protocol (see voice_gateway.tts.TtsClient). This
process is the other end of it, on the workstation GPU: zero-shot clone from a
short reference clip, then stream s16le PCM at 24 kHz so the wire protocol does
not change.

Voices are files, not a glossary. Drop `presence.wav` (five seconds or more) in
the voices directory and ask for voice=presence. The model's built-in speaker
is `default` and needs no file.

Conditionals are prepared once per voice and reused. Re-encoding the reference
on every sentence would put clone cost on the first audible word.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import __version__

log = logging.getLogger(__name__)

# Chatterbox S3GEN_SR. Matches voice_gateway.protocol.TTS_SAMPLE_RATE, which is
# why the client does not have to learn a second rate when this lane is live.
SAMPLE_RATE = 24_000
_VOICE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


class Synth(Protocol):
    """The engine behind the HTTP surface. Tests inject a fake; production
    wraps ChatterboxTurboTTS. generate() returns float32 mono at SAMPLE_RATE."""

    sample_rate: int

    def list_voices(self) -> list[str]: ...

    def generate(self, text: str, voice: str) -> np.ndarray: ...


class SpeechRequest(BaseModel):
    input: str
    model: str = "chatterbox-turbo"
    voice: str = "default"
    response_format: Literal["pcm", "wav"] = "pcm"
    speed: float = 1.0


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8774
    voices_dir: Path = Path.home() / ".local/share/voice-chatter/voices"
    device: str = "auto"
    variant: Literal["turbo", "nano"] = "turbo"
    default_voice: str = "default"


def settings_from_env() -> Settings:
    return Settings(
        host=os.environ.get("CHATTER_HOST") or "0.0.0.0",
        port=int(os.environ.get("CHATTER_PORT") or "8774"),
        voices_dir=Path(os.environ.get("CHATTER_VOICES_DIR") or str(
            Path.home() / ".local/share/voice-chatter/voices"
        )),
        device=os.environ.get("CHATTER_DEVICE") or "auto",
        variant="nano" if (os.environ.get("CHATTER_VARIANT") or "turbo").lower() == "nano" else "turbo",
        default_voice=os.environ.get("CHATTER_DEFAULT_VOICE") or "default",
    )


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - auto-detect must not crash startup
        log.debug("torch device probe failed", exc_info=True)
    return "cpu"


def float_to_pcm16(wav: np.ndarray) -> bytes:
    mono = np.asarray(wav, dtype=np.float32).reshape(-1)
    clipped = np.clip(mono, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buf.getvalue()


def list_voice_files(voices_dir: Path) -> dict[str, Path]:
    if not voices_dir.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(voices_dir.iterdir()):
        if path.suffix.lower() in _AUDIO_SUFFIXES and _VOICE_NAME.fullmatch(path.stem):
            found[path.stem] = path
    return found


class ChatterboxSynth:
    """Zero-shot Chatterbox Turbo/Nano, with per-voice conditionals cached."""

    sample_rate = SAMPLE_RATE

    def __init__(self, *, voices_dir: Path, device: str, variant: str, default_voice: str) -> None:
        self._voices_dir = voices_dir
        self._default_voice = default_voice
        self._lock = threading.Lock()
        self._conds: dict[str, object] = {}
        self._mtimes: dict[str, float] = {}
        log.info("loading Chatterbox %s on %s", variant, device)
        try:
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            self._model = ChatterboxTurboTTS.from_pretrained(
                device=device, nano=(variant == "nano")
            )
        except ImportError:
            from chatterbox.tts import ChatterboxTTS
            log.warning("chatterbox-turbo not installed; using original ChatterboxTTS")
            self._model = ChatterboxTTS.from_pretrained(device=device)
        self.sample_rate = int(self._model.sr)
        if self._model.conds is not None:
            self._conds[default_voice] = self._model.conds
            log.info("built-in speaker registered as voice=%s", default_voice)
        voices_dir.mkdir(parents=True, exist_ok=True)
        for name, path in list_voice_files(voices_dir).items():
            try:
                self._prepare(name, path)
            except Exception:  # noqa: BLE001 - a bad clip must not kill startup
                log.exception("skipping voice %s (%s)", name, path)

    def list_voices(self) -> list[str]:
        names = set(list_voice_files(self._voices_dir))
        names.update(self._conds)
        return sorted(names)

    def generate(self, text: str, voice: str) -> np.ndarray:
        conds = self._conds_for(voice)
        with self._lock:
            self._model.conds = conds
            wav = self._model.generate(text)
        if hasattr(wav, "detach"):
            wav = wav.detach().cpu().numpy()
        return np.asarray(wav, dtype=np.float32).reshape(-1)

    def _conds_for(self, voice: str):
        files = list_voice_files(self._voices_dir)
        path = files.get(voice)
        if path is not None:
            mtime = path.stat().st_mtime
            if self._mtimes.get(voice) != mtime or voice not in self._conds:
                self._prepare(voice, path)
            return self._conds[voice]
        if voice in self._conds:
            return self._conds[voice]
        known = ", ".join(self.list_voices()) or "(none)"
        raise KeyError(f"unknown voice {voice!r}; have {known}")

    def _prepare(self, name: str, path: Path) -> None:
        log.info("cloning voice %s from %s", name, path)
        with self._lock:
            self._model.prepare_conditionals(str(path))
            self._conds[name] = self._model.conds
            self._mtimes[name] = path.stat().st_mtime


class FakeSynth:
    """Deterministic stand-in so the HTTP surface can be tested without torch."""

    sample_rate = SAMPLE_RATE

    def __init__(self, voices: list[str] | None = None) -> None:
        self._voices = list(voices or ["default"])
        self.calls: list[tuple[str, str]] = []

    def list_voices(self) -> list[str]:
        return list(self._voices)

    def generate(self, text: str, voice: str) -> np.ndarray:
        if voice not in self._voices:
            raise KeyError(voice)
        self.calls.append((text, voice))
        # 20 ms of silence per character, so a short "ok" is still a real body.
        n = max(SAMPLE_RATE // 50, min(len(text), 40) * (SAMPLE_RATE // 50))
        return np.zeros(n, dtype=np.float32)


def build_app(synth: Synth, *, default_voice: str = "default") -> FastAPI:
    app = FastAPI(title="voice-chatter", version=__version__)
    app.state.synth = synth
    app.state.default_voice = default_voice

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "voices": synth.list_voices()}

    @app.get("/v1/models")
    def models() -> dict:
        return {
            "object": "list",
            "data": [{"id": "chatterbox-turbo", "object": "model", "owned_by": "local"}],
        }

    @app.post("/v1/audio/speech")
    async def speech(req: SpeechRequest):
        text = req.input.strip()
        if not text:
            raise HTTPException(400, "input is empty")
        voice = req.voice.strip() or default_voice
        if not _VOICE_NAME.fullmatch(voice):
            raise HTTPException(400, "voice must be a filename stem")
        try:
            wav = await asyncio.to_thread(synth.generate, text, voice)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        except AssertionError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - one bad synth, not a dead server
            log.exception("synthesis failed")
            raise HTTPException(500, f"synthesis failed: {exc}") from exc

        pcm = float_to_pcm16(wav)
        if req.response_format == "wav":
            body = pcm16_to_wav(pcm, synth.sample_rate)
            return Response(body, media_type="audio/wav")
        return StreamingResponse(_chunks(pcm), media_type="audio/pcm")

    return app


def _chunks(pcm: bytes, size: int = 4096):
    for i in range(0, len(pcm), size):
        yield pcm[i : i + size]


def make_synth(cfg: Settings) -> Synth:
    device = resolve_device(cfg.device)
    return ChatterboxSynth(
        voices_dir=cfg.voices_dir,
        device=device,
        variant=cfg.variant,
        default_voice=cfg.default_voice,
    )


def create_app(cfg: Settings | None = None, synth: Synth | None = None) -> FastAPI:
    cfg = cfg or settings_from_env()
    return build_app(synth or make_synth(cfg), default_voice=cfg.default_voice)
