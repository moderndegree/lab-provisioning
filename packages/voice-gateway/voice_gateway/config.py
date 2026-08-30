"""Runtime configuration, entirely from the environment.

Every value here is written into the systemd user unit by
`ser5/ansible/roles/voice`. Nothing is read from a file on the box, so the unit
file is the single source of truth for what the gateway is pointed at and
`systemctl --user show voice-gateway -p Environment` is the whole story.

Defaults match the role's defaults/main.yml. They are duplicated rather than
imported because the gateway must also run standing alone from a checkout for
benching, but the ROLE is authoritative — if these two ever disagree, the role
wins and this file is the stale one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _str(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # ─── Where we listen ─────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: _str("VOICE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("VOICE_PORT", 8772))

    # ─── STT: OpenAI-compatible /v1/audio/transcriptions ─────────────────────
    # base.en is the default on MEASUREMENT, not taste. ser5 CPU, 2.25 s
    # command, 2026-08-30: tiny.en 392 ms, base.en 632 ms — and tiny.en heard
    # "the GPU temperature on many" where base.en heard "on Mini". 240 ms is
    # worth paying to get the name of the box right. tiny.en is the knob if that
    # trade ever inverts.
    stt_url: str = field(default_factory=lambda: _str("VOICE_STT_URL", "http://127.0.0.1:8770"))
    stt_model: str = field(
        default_factory=lambda: _str("VOICE_STT_MODEL", "Systran/faster-whisper-base.en")
    )
    # Pinning the language skips whisper's detection pass, a real saving on a
    # short clip. Blank to auto-detect.
    stt_language: str = field(default_factory=lambda: _str("VOICE_STT_LANGUAGE", "en"))

    # ─── TTS: OpenAI-compatible /v1/audio/speech ─────────────────────────────
    # Defaults to the SAME service as STT — speaches serves both. They are kept
    # as two settings so the TTS lane can be repointed without touching STT,
    # which is exactly what a Chatterbox voice-clone lane on the workstation GPU
    # would need.
    tts_url: str = field(default_factory=lambda: _str("VOICE_TTS_URL", "http://127.0.0.1:8770"))
    tts_model: str = field(
        default_factory=lambda: _str("VOICE_TTS_MODEL", "speaches-ai/piper-en_US-amy-medium")
    )
    tts_voice: str = field(default_factory=lambda: _str("VOICE_TTS_VOICE", "en_US-amy-medium"))
    tts_speed: float = field(default_factory=lambda: _float("VOICE_TTS_SPEED", 1.0))

    # ─── LLM: mini's quality instance, straight, no Hermes in the path ───────
    llm_base_url: str = field(
        default_factory=lambda: _str("VOICE_LLM_BASE_URL", "http://mini:8090/v1")
    )
    # Must equal llama-server's --alias exactly. llama-server rejects any other
    # model field, and the failure looks like a 400 with no useful body.
    llm_model: str = field(
        default_factory=lambda: _str("VOICE_LLM_MODEL", "qwen3.6-35b-a3b-mtp")
    )
    # A spoken answer is short. Capping this is not a memory saving — the KV is
    # partitioned statically at server start — it is a LATENCY control: an
    # unbounded answer means unbounded time before "done".
    llm_max_tokens: int = field(default_factory=lambda: _int("VOICE_LLM_MAX_TOKENS", 400))
    llm_temperature: float = field(default_factory=lambda: _float("VOICE_LLM_TEMPERATURE", 0.7))
    # Turns kept in the rolling history, EXCLUDING the system prompt. Small on
    # purpose: the system prompt is the prefix-cache key and every extra turn
    # pushes the cacheable prefix further from the front of the prompt.
    llm_history_turns: int = field(default_factory=lambda: _int("VOICE_LLM_HISTORY_TURNS", 6))
    system_prompt_path: str = field(
        default_factory=lambda: _str("VOICE_SYSTEM_PROMPT_PATH", "")
    )

    # ─── Tools ───────────────────────────────────────────────────────────────
    searxng_url: str = field(
        default_factory=lambda: _str("VOICE_SEARXNG_URL", "http://127.0.0.1:8888/search")
    )
    search_results: int = field(default_factory=lambda: _int("VOICE_SEARCH_RESULTS", 5))
    hermes_bin: str = field(default_factory=lambda: _str("VOICE_HERMES_BIN", "hermes"))
    # Hermes delegations are long by design. This only bounds how long we hold
    # the task before giving up and saying so.
    hermes_timeout: int = field(default_factory=lambda: _int("VOICE_HERMES_TIMEOUT", 900))

    # ─── Endpointing and barge-in ────────────────────────────────────────────
    # Silero VAD as a bare ONNX graph. We deliberately do NOT use the silero-vad
    # pip package: it depends on torch + torchaudio, which is ~2 GB of wheels on
    # a box whose whole job here is to move small buffers between sockets. The
    # role fetches the 2.3 MB .onnx with a checksum instead.
    vad_model_path: str = field(
        default_factory=lambda: _str(
            "VOICE_VAD_MODEL_PATH", "/data/services/voice/models/silero_vad.onnx"
        )
    )
    # Silero speech probability above which a 32 ms window counts as speech.
    vad_threshold: float = field(default_factory=lambda: _float("VOICE_VAD_THRESHOLD", 0.5))
    # Silence after speech before we call the utterance finished. This is the
    # single biggest fixed cost in vad mode, and the whole reason ptt mode
    # exists: a key release is an endpoint with no hangover at all.
    vad_silence_ms: int = field(default_factory=lambda: _int("VOICE_VAD_SILENCE_MS", 250))
    # Speech must persist this long before it counts as a real utterance start,
    # which keeps a cough or a door from opening a turn.
    vad_min_speech_ms: int = field(default_factory=lambda: _int("VOICE_VAD_MIN_SPEECH_MS", 120))
    # ... and this long to interrupt playback. Higher than min_speech_ms because
    # a false barge-in is worse than a slightly late one: it truncates an answer
    # the user was still listening to.
    barge_in_ms: int = field(default_factory=lambda: _int("VOICE_BARGE_IN_MS", 300))
    # Hard cap on a single utterance so a stuck-open mic cannot stream forever.
    max_utterance_ms: int = field(default_factory=lambda: _int("VOICE_MAX_UTTERANCE_MS", 30_000))

    # ─── Sentence chunking — the main latency lever ──────────────────────────
    # We flush the FIRST sentence as soon as it is plausibly a sentence, because
    # that flush is what the user experiences as response time. Later sentences
    # wait for a longer minimum so we stop cutting "Dr." and "e.g." into pieces
    # once the audio pipeline is already busy and nobody is waiting.
    first_chunk_min_chars: int = field(
        default_factory=lambda: _int("VOICE_FIRST_CHUNK_MIN_CHARS", 12)
    )
    chunk_min_chars: int = field(default_factory=lambda: _int("VOICE_CHUNK_MIN_CHARS", 40))
    # Flush regardless once the buffer gets this long, so a model that never
    # emits terminal punctuation still produces audio.
    chunk_max_chars: int = field(default_factory=lambda: _int("VOICE_CHUNK_MAX_CHARS", 240))
    # Let the FIRST chunk end at a comma or colon, not only at a full stop.
    #
    # OFF, because it was measured and it LOST. ser5, 2026-08-30, n=8, identical
    # stimulus, time to first audible word:
    #     clause-break off   1235 ms   (first sentence 284 ms)
    #     clause-break on    1390 ms   (first sentence 358 ms)
    # It was built to buy latency and did not, while also giving piper a falling
    # end-of-sentence intonation on a sentence fragment. What actually shortened
    # the first sentence was the "LEAD WITH THE ANSWER" instruction in the
    # system prompt: 532 ms -> 284 ms.
    #
    # Kept as a flag rather than deleted because it may well win on a chattier
    # model, and the next person to wonder should re-run voicebench rather than
    # re-implement it.
    first_chunk_clause_break: bool = field(
        default_factory=lambda: _bool("VOICE_FIRST_CHUNK_CLAUSE_BREAK", False)
    )

    # ─── Seams left open on purpose ──────────────────────────────────────────
    # Native OpenAI tool-calling against mini. OFF by default: a tool call costs
    # a SECOND round trip through the model before the first audible word, which
    # doubles the number this whole design exists to minimise. Deterministic
    # routing (router.py) handles the two tools we actually have. Turn this on
    # only with a voicebench number that justifies it.
    native_tools: bool = field(default_factory=lambda: _bool("VOICE_NATIVE_TOOLS", False))

    request_timeout: float = field(default_factory=lambda: _float("VOICE_REQUEST_TIMEOUT", 30.0))
    log_level: str = field(default_factory=lambda: _str("VOICE_LOG_LEVEL", "INFO"))


def load() -> Config:
    return Config()
