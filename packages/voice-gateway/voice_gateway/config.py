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

    # ─── The Ledger — the only place state lives ─────────────────────────────
    # Durable conversation, briefs, results and egress. Under {data_mount}/services
    # /voice, which is already inside restic's /data, so there is no new backup
    # path to remember. Before this existed, conversation was a Python list on the
    # WebSocket: a dropped wifi frame — on a lab that is wifi-only on both boxes —
    # silently started the conversation over, and the phone and the workstation
    # were two assistants that had never met.
    ledger_path: str = field(
        default_factory=lambda: _str(
            "VOICE_LEDGER_PATH", "/data/services/voice/ledger.db"
        )
    )
    # Closed briefs are exported here as markdown so cortex MCP and Obsidian can
    # read them (docs/brain.md: files are sacred, indexes are disposable — the DB
    # is the index, the exported brief is the file). Turns are NOT exported;
    # exporting conversational chatter would flood the vault. Blank to disable.
    vault_dir: str = field(default_factory=lambda: _str("VOICE_VAULT_DIR", "/data/brain"))
    # How long a finished result stays worth speaking. Past this it is not
    # volunteered — only answered for if asked. Speaking the result of a question
    # asked yesterday, unprompted, is worse than not answering.
    result_ttl_s: float = field(
        default_factory=lambda: _float("VOICE_RESULT_TTL_S", 6 * 3600.0)
    )

    # ─── Tools ───────────────────────────────────────────────────────────────
    # THE PRESENCE MUST NOT TOUCH THE NETWORK. If the fast path can reach the
    # internet then the fast path's latency IS the internet's latency, and the
    # response guarantee is gone — a search turn measured ~1.6s of SearXNG round
    # trip sitting directly on a budget whose whole target is under 600ms.
    #
    # TRUE today only because the Bench does not exist yet, so turning it off now
    # would take a working feature dark for nothing. It flips to false when the
    # bench lands and this flag is deleted with it. Set it false to measure the
    # "no network at all" phase honestly in the meantime.
    presence_network: bool = field(
        default_factory=lambda: _bool("VOICE_PRESENCE_NETWORK", True)
    )
    searxng_url: str = field(
        default_factory=lambda: _str("VOICE_SEARXNG_URL", "http://127.0.0.1:8888/search")
    )
    search_results: int = field(default_factory=lambda: _int("VOICE_SEARCH_RESULTS", 5))
    hermes_bin: str = field(default_factory=lambda: _str("VOICE_HERMES_BIN", "hermes"))
    # Hermes delegations are long by design. This only bounds how long we hold
    # the task before giving up and saying so.
    hermes_timeout: int = field(default_factory=lambda: _int("VOICE_HERMES_TIMEOUT", 900))

    # ─── The Bench — deep work, and the only route off the box ───────────────
    # A separate process (voice-bench.service) sharing only the Ledger. Off by
    # default: with no bench running, a dispatched brief would sit pending
    # forever and the presence would have said "on it" about work nobody was
    # doing. That is the one thing the strategy forbids outright — the system is
    # allowed to be shallow, it is never allowed to claim an errand is finished,
    # or even started, when it isn't.
    bench_enabled: bool = field(default_factory=lambda: _bool("VOICE_BENCH_ENABLED", False))
    # :8091, not :8090. The deep instance is one slot of qwen3.8-27b at ctx
    # 262144; the presence's is four slots of a 35B MoE picked for decode speed.
    # Two processes, one GPU — which is exactly what `voicebench --load` measures.
    bench_llm_base_url: str = field(
        default_factory=lambda: _str("VOICE_BENCH_LLM_BASE_URL", "http://mini:8091/v1")
    )
    bench_llm_model: str = field(
        default_factory=lambda: _str("VOICE_BENCH_LLM_MODEL", "qwen3.8-27b")
    )
    # Ten times the presence's cap, and for the opposite reason: nobody is
    # waiting on this, so the bound exists only to stop one runaway errand from
    # holding the single deep slot indefinitely.
    bench_llm_max_tokens: int = field(
        default_factory=lambda: _int("VOICE_BENCH_LLM_MAX_TOKENS", 4000)
    )
    # Reasoning is the entire reason the deep tier exists. Contrast the presence,
    # which turns it off because a small cap consumed by reasoning returns empty
    # content — silence, then nothing.
    bench_thinking: bool = field(default_factory=lambda: _bool("VOICE_BENCH_THINKING", True))
    bench_poll_s: float = field(default_factory=lambda: _float("VOICE_BENCH_POLL_S", 2.0))

    # Hermes is ONE capability behind the bench, not the bench's policy point.
    # Named explicitly on every invocation because it cannot be version-pinned
    # (the installer always fetches latest) and four hosted credentials remain
    # live on this box — see docs/todo.md and bench/hermes.py.
    hermes_provider: str = field(default_factory=lambda: _str("VOICE_HERMES_PROVIDER", "custom"))
    hermes_model: str = field(default_factory=lambda: _str("VOICE_HERMES_MODEL", ""))

    # ─── Hands — acting on real systems ──────────────────────────────────────
    # OFF by default. The shipped registry contains only read-only actions, so
    # this is not protecting against the actions themselves — it is protecting
    # against the idea. Turning voice into a control plane for a box should be a
    # thing someone decided, not a thing that was already true.
    hands_enabled: bool = field(default_factory=lambda: _bool("VOICE_HANDS_ENABLED", False))
    # How long a spoken "yes" can still mean yes. Past it the presence asks
    # again, which is cheap; acting on a stale confirmation that was probably
    # about something else is not.
    hands_consent_s: float = field(default_factory=lambda: _float("VOICE_HANDS_CONSENT_S", 30.0))

    # ─── The floor — how long the presence holds the GPU against the bench ───
    # Measured on ser5 2026-08-30: one deep generation on :8091 makes the
    # presence on :8090 2.8x slower (first audible word 861ms -> 2451ms). The
    # two llama-server processes share one GPU and do not isolate, so the bench
    # has to yield rather than merely be niced.
    #
    # A DEADLINE, NOT A LOCK. The presence pushes it forward while it holds the
    # floor and never releases it, so a crashed gateway frees the bench by itself
    # after this many seconds. 5s comfortably covers a turn (median first word
    # 576ms) plus the gap between two quick ones, without parking the bench for
    # long after a conversation stops.
    floor_hold_s: float = field(default_factory=lambda: _float("VOICE_FLOOR_HOLD_S", 5.0))

    # ─── Locality, self-proven ───────────────────────────────────────────────
    # When the nftables rule is deployed, the presence checks on ITSELF that it
    # cannot leave the property, and reports it on /v1/status.
    #
    # WHY THE PRESENCE AND NOT A VERIFY TASK. The rule matches a cgroup, so the
    # only process that can honestly test it is one already inside that cgroup.
    # An external probe has to get itself in there, and the obvious ways do not
    # work: `nsenter --cgroup` enters the cgroup NAMESPACE and leaves the process
    # in its own cgroup (measured on ser5 2026-08-30 — the probe stayed in
    # session-260.scope and sailed straight out to the internet, reporting a
    # false failure), while writing to cgroup.procs needs root because cgroup v2
    # delegation containment requires write access to the common ancestor.
    # The presence is already in the right cgroup. It should just answer.
    #
    # A literal IP, never a hostname: DNS would make this a test of the resolver.
    locality_probe_host: str = field(
        default_factory=lambda: _str("VOICE_LOCALITY_PROBE_HOST", "1.1.1.1")
    )
    locality_probe_port: int = field(
        default_factory=lambda: _int("VOICE_LOCALITY_PROBE_PORT", 443)
    )
    # Short. A rejected connection fails instantly (the rule rejects with
    # icmp admin-prohibited rather than dropping, precisely so this is fast and
    # unambiguous); anything slower than this is a timeout, which proves nothing
    # either way and is reported as inconclusive rather than as a pass.
    locality_probe_timeout: float = field(
        default_factory=lambda: _float("VOICE_LOCALITY_PROBE_TIMEOUT", 2.0)
    )
    # Whether egress from THIS process is supposed to be impossible. Set from the
    # role's voice_locality_enforce, so the check knows whether "I reached the
    # internet" is a finding or just the current design.
    locality_enforced: bool = field(
        default_factory=lambda: _bool("VOICE_LOCALITY_ENFORCED", False)
    )

    # ─── Return — when a finished result re-enters the conversation ──────────
    # A seam, never mid-turn. This is how long the presence must have been idle
    # before it will volunteer one: long enough that it is not interrupting,
    # short enough that "twenty minutes later" still feels like an answer rather
    # than an ambush.
    return_quiet_s: float = field(default_factory=lambda: _float("VOICE_RETURN_QUIET_S", 4.0))
    return_poll_s: float = field(default_factory=lambda: _float("VOICE_RETURN_POLL_S", 5.0))

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

    # How long a closing session waits for a turn to finish persisting before it
    # is cancelled. See Session._teardown: `done` goes out before the Ledger
    # write, so a client that hangs up on hearing the answer can catch a turn
    # mid-INSERT.
    teardown_grace_s: float = field(
        default_factory=lambda: _float("VOICE_TEARDOWN_GRACE_S", 2.0)
    )

    request_timeout: float = field(default_factory=lambda: _float("VOICE_REQUEST_TIMEOUT", 30.0))
    log_level: str = field(default_factory=lambda: _str("VOICE_LOG_LEVEL", "INFO"))


def load() -> Config:
    return Config()
