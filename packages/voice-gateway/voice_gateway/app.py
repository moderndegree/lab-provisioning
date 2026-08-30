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

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket

from . import __version__
from .config import Config, load
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


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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
            search=WebSearch(cfg.searxng_url, results=cfg.search_results),
            hermes=HermesDelegate(cfg.hermes_bin, timeout=cfg.hermes_timeout),
            system_prompt=_system_prompt(cfg),
        )
        app.state.cfg = cfg
        app.state.deps = deps
        log.info(
            "voice-gateway %s up: llm=%s model=%s stt=%s tts=%s",
            __version__,
            cfg.llm_base_url,
            cfg.llm_model,
            cfg.stt_url,
            cfg.tts_url,
        )
        try:
            yield
        finally:
            await deps.stt.aclose()
            await deps.llm.aclose()
            await deps.tts.aclose()
            await deps.search.aclose()

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

        checks["search"] = {
            "ok": await deps.search.health(),
            "detail": cfg.searxng_url,
        }
        checks["hermes"] = {
            "ok": deps.hermes.available(),
            "detail": f"{cfg.hermes_bin} on the unit PATH",
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
