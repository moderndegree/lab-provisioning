"""The brief loop. Claim a brief, work it, put the result where Return can find it.

Deliberately not an agent. It is two phases with a re-read between them:

    gather   if the brief needs retrieval, fetch it and record the egress
    answer   ask the deep model, streaming, abandoning if the brief changes

An agent loop would be a bigger claim than this system can currently keep. Two
phases is enough to be genuinely useful, and every step of it is checkpointed
into `brief_events`, so an errand that produced a surprising answer can be read
back as the sequence of things that actually happened rather than guessed at.

IT YIELDS THE GPU TO THE PRESENCE, AND THAT IS NOT OPTIONAL. Measured on ser5
2026-08-30: one deep generation here makes the presence 2.8x slower — first
audible word 861ms idle against 2451ms with a single errand running, and
model-to-first-sentence 498ms against 2041ms. :8090 and :8091 are two
llama-server processes sharing one GPU and they do not isolate; the unit's
Nice/CPUWeight do nothing about it because the contention is not CPU.

So the presence takes a floor in the Ledger at the moment of intent — the
client's `start` frame, sent at key-press before the sentence is finished — and
this abandons whatever it was generating within a few dozen tokens, then waits.
The errand loses the tokens it had already paid for and starts that phase again.
That is the trade, stated plainly: errands get slower, and sometimes much
slower, so that talking never does.

STEERABILITY IS THE POINT, AND IT COSTS YIELDING. The brief is re-read before
each phase and again every few dozen tokens mid-generation. When its revision has
moved, the work in flight is abandoned and restarted from the new statement. That
makes the deep tier slower than its peak — a restarted answer paid for the tokens
it threw away — and the strategy accepts that trade explicitly: deep work slower
than its peak, because steerability requires yielding.

WHAT IT WILL NOT DO. It will not speak. It has no WebSocket, no TTS client, and
no way to reach either. A result becomes audible only when the presence picks it
up from the Ledger at a seam and says it in its own voice.
"""

from __future__ import annotations

import asyncio
import logging

from ..export import VaultExporter
from ..ledger import (
    BRIEF_CANCELLED,
    BRIEF_DONE,
    BRIEF_FAILED,
    EVENT_CHECKPOINT,
    EVENT_TOOL,
    Brief,
    Ledger,
)
from ..llm import LlmClient
from .hermes import HermesDelegate
from .web_search import WebSearch

log = logging.getLogger(__name__)


class FloorTaken(Exception):
    """The presence wants the GPU. Raised out of a generation, mid-stream.

    An exception rather than a quiet `return`, because a yield and a completion
    must never be mistaken for one another: a partial answer accepted as final
    would be the system claiming an errand is finished when it isn't, which is
    the one thing the strategy forbids outright.
    """

# The deep tier is told, at the top of every brief, that its output will be READ
# ALOUD by something else. Without this it writes markdown with headings and
# bullet lists, and the presence ends up speaking the word "asterisk".
SYSTEM = """You are the bench of a private AI home lab: the part that does slow,
careful work while a separate voice keeps the conversation going.

Your answer will be SPOKEN ALOUD by that voice, later, to the person who asked.
Write it as something to be heard: no markdown, no headings, no bullet points, no
code blocks, no URLs. Name a source rather than reading its address.

Lead with the finding. Six sentences at most — the listener asked a question, not
for a report. If the evidence does not support an answer, say what you found and
what is still missing, rather than filling the gap."""


class Bench:
    def __init__(
        self,
        *,
        ledger: Ledger,
        llm: LlmClient,
        search: WebSearch | None,
        hermes: HermesDelegate | None,
        exporter: VaultExporter,
        result_ttl_s: float,
        poll_s: float = 2.0,
    ) -> None:
        self._ledger = ledger
        self._llm = llm
        self._search = search
        self._hermes = hermes
        self._exporter = exporter
        self._ttl = result_ttl_s
        self._poll = poll_s

    # ─── the loop ────────────────────────────────────────────────────────────
    async def run_forever(self) -> None:
        # Anything left `running` belongs to a bench that is no longer here —
        # this is the only one. Without this a crash, an OOM kill, or a converge
        # that restarts the unit strands the errand forever: it is not pending,
        # so nothing claims it, and it is not done, so nothing returns it. The
        # presence has already said "on it".
        requeued = await self._ledger.requeue_running()
        if requeued:
            log.warning("requeued %d brief(s) abandoned by a previous bench", requeued)
        log.info("bench up, polling for briefs every %.1fs", self._poll)
        while True:
            try:
                # Never START an errand while the presence has the floor. Aborting
                # one mid-stream is cheap but not free, and beginning one during a
                # conversation is a self-inflicted version of exactly the
                # contention this loop exists to avoid.
                if await self._ledger.floor_taken():
                    await asyncio.sleep(self._poll)
                    continue
                brief = await self._ledger.claim_brief()
            except Exception:  # noqa: BLE001 - a bad poll is not a dead bench
                log.exception("claim failed")
                brief = None
            if brief is None:
                await asyncio.sleep(self._poll)
                continue
            await self.work(brief)

    async def work(self, brief: Brief) -> None:
        log.info("brief %s claimed (%s): %s", brief.id, brief.capability or "deep",
                 brief.statement[:120])
        try:
            # Restarted, not resumed, when the brief moves under us. A partially
            # gathered answer to the old question is worse than nothing: it reads
            # as the system having ignored the correction.
            for attempt in range(1, 4):
                revision = brief.revision
                try:
                    answer = await self._attempt(brief)
                except FloorTaken:
                    # Not a failure and not a restart — the brief has not moved,
                    # someone is just talking. Wait for the floor and try the
                    # same wording again. The brief stays CLAIMED throughout, so
                    # nothing else picks it up and the presence keeps the GPU.
                    await self._ledger.add_event(
                        brief.id, EVENT_CHECKPOINT, {"yielded": "presence took the floor"}
                    )
                    await self._await_floor()
                    brief = await self._ledger.get_brief(brief.id) or brief
                    continue
                current = await self._ledger.get_brief(brief.id)
                if current is None or current.state == BRIEF_CANCELLED:
                    log.info("brief %s went away mid-flight", brief.id)
                    return
                if current.revision == revision:
                    await self._finish(brief.id, answer)
                    return
                log.info(
                    "brief %s moved r%d -> r%d during attempt %d; restarting",
                    brief.id, revision, current.revision, attempt,
                )
                await self._ledger.add_event(
                    brief.id, EVENT_CHECKPOINT,
                    {"restarted": attempt, "from_revision": revision,
                     "to_revision": current.revision},
                )
                brief = current
            # Three restarts means the statement is moving faster than the work.
            # Answering the newest wording is the honest response; pretending the
            # churn did not happen is not, so it is on the record.
            await self._ledger.add_event(
                brief.id, EVENT_CHECKPOINT, {"note": "amended faster than worked"}
            )
            await self._finish(brief.id, await self._attempt(brief))
        except asyncio.CancelledError:
            # The unit is stopping. Put the brief back rather than losing it —
            # the row IS the handoff, and an interrupted bench must not silently
            # swallow work someone is still waiting on.
            await self._ledger.close_brief(brief.id, "pending")
            raise
        except Exception as exc:  # noqa: BLE001 - one bad brief, not a dead bench
            log.exception("brief %s failed", brief.id)
            # str() on several httpx errors is EMPTY, which produced the spoken
            # sentence "That one failed:" followed by nothing — a result that
            # tells the listener less than silence would. The class name is at
            # least a fact: "ConnectError" says which way it broke.
            why = str(exc).strip() or type(exc).__name__
            await self._ledger.add_event(brief.id, EVENT_CHECKPOINT, {"error": why})
            await self._ledger.record_result(
                brief.id, f"That one failed: {why}", ttl_s=self._ttl
            )
            await self._ledger.close_brief(brief.id, BRIEF_FAILED)

    # ─── one attempt at the current wording ──────────────────────────────────
    async def _attempt(self, brief: Brief) -> str:
        if brief.capability == "delegation":
            return await self._delegate(brief)

        context = ""
        if brief.capability == "retrieval":
            context = await self._gather(brief)

        prompt = brief.statement if not context else (
            f"{context}\n\nUsing those results, answer: {brief.statement}"
        )
        answer = await self._llm.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            should_continue=self._unchanged(brief),
        )
        await self._ledger.add_event(
            brief.id, EVENT_CHECKPOINT,
            {"phase": "answer", "revision": brief.revision, "chars": len(answer)},
        )
        return answer or "The deep model came back with nothing."

    async def _gather(self, brief: Brief) -> str:
        if self._search is None:
            return "No retrieval is configured, so this was answered from memory alone."
        # THE QUERY IS THE BRIEF'S STATEMENT, which Judgment already reduced to
        # terms — "search for strix halo benchmarks" became "strix halo
        # benchmarks" before this row was ever written. The utterance itself
        # never reaches here and there is no code path that could carry it.
        results = await self._search.search(brief.statement, brief_id=brief.id)
        await self._ledger.add_event(
            brief.id, EVENT_TOOL,
            {"tool": "search", "query": brief.statement, "results": len(results)},
        )
        return WebSearch.as_context(brief.statement, results)

    async def _delegate(self, brief: Brief) -> str:
        if self._hermes is None or not self._hermes.available():
            return "Hermes is not available on this box, so that could not be handed off."
        out = await self._hermes.run(brief.statement)
        await self._ledger.add_event(
            brief.id, EVENT_TOOL, {"tool": "hermes", "chars": len(out)}
        )
        return out

    async def _await_floor(self) -> None:
        """Block until the presence is done with the GPU."""
        waited = 0.0
        while await self._ledger.floor_taken():
            await asyncio.sleep(self._poll)
            waited += self._poll
        if waited:
            log.info("floor free after %.1fs; resuming", waited)

    def _unchanged(self, brief: Brief):
        """Polled mid-generation: is this still my brief, and may I still have the GPU?

        One Ledger read answers both, because both are asked at the same moments
        and a second round trip would put twice the disk in a hot loop.

        Returning False means the BRIEF moved — the generation stops and the work
        restarts from the new wording. Raising FloorTaken means the PRESENCE
        moved — the generation stops and the same wording is retried later.
        Conflating them would either throw away a correction or accept a
        half-finished answer as final.
        """

        async def check() -> bool:
            revision, floor = await self._ledger.brief_status(brief.id)
            if floor:
                raise FloorTaken
            return revision is not None and revision == brief.revision
        return check

    async def _finish(self, brief_id: str, answer: str) -> None:
        """Result, note, then done — in that order.

        `state = done` is written LAST so that it means what a reader assumes it
        means: everything about this errand is finished, including its note in
        the vault. Closing first left a window where the brief looked complete
        while the export was still running, and anything polling on state — the
        Return loop, a person watching sqlite — would act on a half-finished
        errand.

        The export still cannot fail the errand. The Ledger is the authoritative
        record; the note is for humans reading /data/brain later.
        """
        await self._ledger.record_result(brief_id, answer, ttl_s=self._ttl)
        if self._exporter.enabled:
            await self._exporter.export(brief_id)
            await self._ledger.mark_exported(brief_id)
        await self._ledger.close_brief(brief_id, BRIEF_DONE)
        log.info("brief %s done (%d chars)", brief_id, len(answer))
