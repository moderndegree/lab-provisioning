"""The HTTP/WebSocket surface.

Three endpoints and no more:

  GET /health       liveness. Cheap, no dependencies touched.
  GET /v1/status    readiness, by ASKING each dependency rather than assuming.
                    roles/voice/tasks/verify.yml consumes this, which is why it
                    reports per-dependency detail instead of one boolean: the
                    verify playbook turns each false into its own finding line.
  WS  /v1/stream    the voice loop.

The split between /health and /v1/status is deliberate and comes from a mistake
this lab has already paid for. In the Open WebUI role: "the UI still loads
perfectly and simply offers no models — a failure that looks like a UI bug from
the browser and produces no error anywhere on ser5". A gateway whose STT is dead
answers /health perfectly too. /v1/status is the endpoint that refuses to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket

from . import __version__
from .config import Config, load
from .delivery import Returner
from .doorman import Doorman
from .hands import Hands, default_registry
from .ledger import Ledger
from .llm import LlmClient
from .session import Deps, Session
from .stt import SttClient
from .tools import HermesDelegate, WebSearch
from .tts import TtsClient

log = logging.getLogger(__name__)

# Kept short on purpose, and it is the prefix-cache key: every turn sends this
# first, byte for byte. Editing it invalidates the cache on mini, which costs
# roughly 12x on the next turn's time-to-first-token. The role ships an
# overridable copy at files/AGENTS-voice.md.
DEFAULT_SYSTEM_PROMPT = """You are the voice assistant for a private AI home lab.

You are being LISTENED TO, not read. Answer in one to three short sentences.
No markdown, no bullet points, no code blocks, no URLs read aloud — describe a
source by name instead. Numbers spoken plainly.

LEAD WITH THE ANSWER. Put the actual answer in the first few words, then add
detail. Never open with a preamble, a restatement of the question, or a hedge —
the first clause is spoken aloud before the rest has finished generating, so a
wasted opening is dead air the listener sits through.

If you do not know, say so in one sentence rather than guessing. If a question
needs current information and no web results were provided, say that.

The lab: mini is the inference box (llama-server, Tier L, sovereign). ser5 is
the always-on driver running Hermes, Open WebUI, SearXNG, Prometheus and this
gateway. The workstation is the cockpit. Everything is reachable over Tailscale
and nothing is exposed to the internet."""


def _system_prompt(cfg: Config) -> str:
    if cfg.system_prompt_path:
        path = Path(cfg.system_prompt_path)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        log.warning("system prompt %s missing; using the built-in", path)
    return DEFAULT_SYSTEM_PROMPT


async def _locality(cfg: Config) -> tuple[str, str]:
    """Try to reach the public internet from inside the presence's own cgroup.

    Three outcomes, and the third is not a pass:

        enforced      the connection was refused immediately — the wall is there
        NOT enforced  the connection SUCCEEDED. The nftables rule is loaded and
                      matching nothing, which is its most likely failure and the
                      one that looks perfect in `nft list ruleset`
        inconclusive  it timed out. That is what an unplugged network looks like
                      too, so it proves nothing and must not be reported as ok

    A bare TCP handshake to a literal IP, immediately closed: no DNS, no bytes,
    no payload. When the rule is working nothing leaves at all.
    """
    loop = asyncio.get_running_loop()
    target = (cfg.locality_probe_host, cfg.locality_probe_port)
    started = loop.time()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(*target), timeout=cfg.locality_probe_timeout
        )
    except (TimeoutError, asyncio.TimeoutError):
        return "inconclusive", (
            f"{target[0]}:{target[1]} timed out after "
            f"{cfg.locality_probe_timeout:.0f}s — cannot tell a firewall from an "
            f"unplugged network, so this proves nothing"
        )
    except OSError as exc:
        return "enforced", (
            f"egress to {target[0]}:{target[1]} refused in "
            f"{(loop.time() - started) * 1000:.0f}ms ({type(exc).__name__})"
        )
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return "NOT enforced", (
        f"the presence REACHED {target[0]}:{target[1]}. voice_locality_enforce is "
        f"on, so the nftables rule is loaded and matching nothing — check "
        f"voice_locality_cgroup_level and voice_locality_cgroup against "
        f"`systemctl --user show voice-gateway -p ControlGroup --value`"
    )


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ledger = Ledger(cfg.ledger_path)
        try:
            await ledger.open()
        except Exception:  # noqa: BLE001
            # Deliberately not fatal. The presence must come up and hold the
            # floor even with no durable store behind it; /v1/status is where
            # that shows, and the Doorman admits clients without memory rather
            # than refusing them. Failing to boot here would mean a full-disk
            # /data takes the voice down entirely.
            log.exception("ledger failed to open at %s; running without memory",
                          cfg.ledger_path)
        deps = Deps(
            stt=SttClient(
                cfg.stt_url,
                model=cfg.stt_model,
                language=cfg.stt_language,
                timeout=cfg.request_timeout,
            ),
            llm=LlmClient(
                cfg.llm_base_url,
                model=cfg.llm_model,
                max_tokens=cfg.llm_max_tokens,
                temperature=cfg.llm_temperature,
            ),
            tts=TtsClient(
                cfg.tts_url,
                model=cfg.tts_model,
                voice=cfg.tts_voice,
                speed=cfg.tts_speed,
                timeout=cfg.request_timeout,
            ),
            ledger=ledger,
            doorman=Doorman(ledger, history_turns=cfg.llm_history_turns),
            returner=Returner(ledger, quiet_s=cfg.return_quiet_s),
            hands=(
                Hands(default_registry(), consent_window_s=cfg.hands_consent_s)
                if cfg.hands_enabled
                else None
            ),
            search=WebSearch(cfg.searxng_url, results=cfg.search_results),
            hermes=HermesDelegate(cfg.hermes_bin, timeout=cfg.hermes_timeout),
            system_prompt=_system_prompt(cfg),
        )
        app.state.cfg = cfg
        app.state.deps = deps
        log.info(
            "voice-gateway %s up: llm=%s model=%s stt=%s tts=%s ledger=%s",
            __version__,
            cfg.llm_base_url,
            cfg.llm_model,
            cfg.stt_url,
            cfg.tts_url,
            cfg.ledger_path,
        )
        try:
            yield
        finally:
            await deps.stt.aclose()
            await deps.llm.aclose()
            await deps.tts.aclose()
            await deps.search.aclose()
            await ledger.aclose()

    app = FastAPI(title="voice-gateway", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/status")
    async def status() -> dict:
        deps: Deps = app.state.deps
        checks: dict[str, dict] = {}

        # LLM — not just "is it up" but "is it serving the alias we send".
        # Network=host on mini means any process can hold :8090; /v1/models is
        # the server's own answer to "who am I".
        try:
            served = await deps.llm.models()
            checks["llm"] = {
                "ok": cfg.llm_model in served,
                "detail": f"serving {served or ['nothing']}, expected {cfg.llm_model}",
            }
        except Exception as exc:  # noqa: BLE001
            checks["llm"] = {"ok": False, "detail": f"{cfg.llm_base_url}: {exc}"}

        # STT — a real transcription. The port accepting TCP proves nothing:
        # speaches answers HTTP long before a model is in its cache, and a
        # missing model shows up only when something asks it to transcribe.
        ok, detail = await deps.stt.health()
        checks["stt"] = {"ok": ok, "detail": detail}

        # TTS — a real synthesis, because it answers HTTP long before piper has
        # a usable voice on disk.
        ok = await deps.tts.health()
        checks["tts"] = {
            "ok": ok,
            "detail": f"{cfg.tts_url} voice={cfg.tts_voice}",
        }

        # Ledger — the only place state lives, so a gateway that talks
        # beautifully and remembers nothing must not report ok. It asks the file
        # a real question rather than checking that the path exists.
        ok, detail = await deps.ledger.health()
        checks["ledger"] = {"ok": ok, "detail": detail}

        # Locality — the presence testing, on itself, that it cannot leave.
        # Only meaningful when the rule is deployed; when it is not, the honest
        # answer is that nothing is claimed, so the check is absent rather than
        # green.
        if cfg.locality_enforced:
            state, detail = await _locality(cfg)
            checks["locality"] = {"ok": state == "enforced", "detail": detail}

        checks["search"] = {
            "ok": await deps.search.health(),
            "detail": cfg.searxng_url,
        }
        checks["hermes"] = {
            "ok": deps.hermes.available(),
            "detail": f"{cfg.hermes_bin} on the unit PATH",
        }

        # The bench is a separate unit, so the gateway cannot see it directly.
        # What it CAN see is whether briefs are being claimed: a pending brief
        # older than a few minutes means the presence said "on it" about work
        # nothing is doing, which is the one failure mode the strategy forbids
        # outright and which every other check here would report as healthy.
        if cfg.bench_enabled:
            stalled = await deps.ledger.stalled_briefs(older_than_s=300)
            checks["bench"] = {
                "ok": not stalled,
                "detail": (
                    f"{len(stalled)} brief(s) pending over 5 min — is "
                    f"voice-bench.service running?"
                    if stalled
                    else "no stalled briefs"
                ),
            }

        return {
            "ok": all(c["ok"] for c in checks.values()),
            "version": __version__,
            "checks": checks,
        }

    @app.websocket("/v1/stream")
    async def stream(ws: WebSocket) -> None:
        await Session(ws, cfg, app.state.deps).run()

    return app
