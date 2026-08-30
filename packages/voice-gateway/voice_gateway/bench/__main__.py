"""Entry point for the bench. `python -m voice_gateway.bench`.

A second process beside the gateway, sharing only the Ledger. It takes no
arguments and opens no listening socket: everything it needs arrives as
environment variables from `voice-bench.service`, and everything it does arrives
as rows in a SQLite file. There is nothing to connect to it and nothing it can
say.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from ..config import load
from ..export import VaultExporter
from ..ledger import Ledger
from ..llm import LlmClient
from .hermes import HermesDelegate
from .runner import Bench
from .web_search import WebSearch

log = logging.getLogger(__name__)


async def _run() -> None:
    cfg = load()
    ledger = Ledger(cfg.ledger_path)
    await ledger.open()
    llm = LlmClient(
        cfg.bench_llm_base_url,
        model=cfg.bench_llm_model,
        max_tokens=cfg.bench_llm_max_tokens,
        temperature=cfg.llm_temperature,
        # Long: a deep answer with reasoning on can take minutes, and nobody is
        # listening to the silence.
        timeout=900.0,
        thinking=cfg.bench_thinking,
    )
    # The ledger is handed to WebSearch, not held beside it, so that recording
    # the egress is not something a caller can forget to do.
    search = WebSearch(cfg.searxng_url, results=cfg.search_results, ledger=ledger)
    hermes = HermesDelegate(
        cfg.hermes_bin,
        timeout=cfg.hermes_timeout,
        provider=cfg.hermes_provider,
        base_url=cfg.bench_llm_base_url,
        model=cfg.hermes_model,
    )
    bench = Bench(
        ledger=ledger,
        llm=llm,
        search=search,
        hermes=hermes,
        exporter=VaultExporter(cfg.vault_dir, ledger),
        result_ttl_s=cfg.result_ttl_s,
        poll_s=cfg.bench_poll_s,
    )
    log.info(
        "voice-bench up: llm=%s model=%s thinking=%s ledger=%s",
        cfg.bench_llm_base_url, cfg.bench_llm_model, cfg.bench_thinking, cfg.ledger_path,
    )
    # SIGTERM is how systemd stops this — on a converge, a reboot, or a manual
    # restart — and it arrives while a brief is very likely in flight. Without a
    # handler the process dies mid-errand and leaves the brief `running`, which
    # is invisible to everything: not pending so nothing claims it, not done so
    # nothing returns it. `requeue_running()` at the next startup would recover
    # it, but cancelling cleanly puts it straight back to pending now, which is
    # what Bench.work's CancelledError path exists for.
    task = asyncio.ensure_future(bench.run_forever())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        log.info("voice-bench stopping; any brief in flight is back to pending")
    finally:
        await llm.aclose()
        await search.aclose()
        await ledger.aclose()


def main() -> None:
    cfg = load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
