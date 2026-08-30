"""The bench: a brief becomes a result, and an amendment mid-flight changes it.

The second of those is the strategy's own acceptance criterion for deep work —
"an errand amended mid-flight demonstrably changes its output" — so it is
asserted on the prompts the model actually received, not on the wording of the
brief.
"""

from __future__ import annotations

import asyncio

import pytest

from voice_gateway.bench.runner import Bench
from voice_gateway.delivery import Returner
from voice_gateway.export import VaultExporter
from voice_gateway.ledger import BRIEF_DONE, Ledger
from voice_gateway.llm import LlmClient

pytestmark = pytest.mark.asyncio


class SlowLlm:
    """Streams slowly enough that a brief can be amended while it generates."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, messages):
        self.prompts.append(messages[-1]["content"])
        for i in range(200):
            await asyncio.sleep(0.002)
            yield f"tok{i} "

    async def complete(self, messages, *, should_continue=None, check_every=40):
        return await LlmClient.complete(
            self, messages, should_continue=should_continue, check_every=check_every
        )


class Search:
    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger
        self.calls: list[tuple[str, str | None]] = []

    async def search(self, query: str, *, brief_id: str | None = None):
        self.calls.append((query, brief_id))
        await self._ledger.record_egress(brief_id, "http://127.0.0.1:8888/search", query)
        return [{"title": "Strix Halo numbers", "url": "http://x", "snippet": "87 tok/s"}]


async def test_an_amended_brief_changes_what_the_model_is_asked(tmp_path):
    ledger = Ledger(str(tmp_path / "l.db"))
    await ledger.open()
    llm, search = SlowLlm(), Search(ledger)
    bench = Bench(
        ledger=ledger, llm=llm, search=search, hermes=None,
        exporter=VaultExporter(str(tmp_path / "brain"), ledger),
        result_ttl_s=3600, poll_s=0.05,
    )

    conversation = await ledger.conversation_for("brian")
    brief_id = await ledger.open_brief(
        conversation_id=conversation, statement="strix halo backend benchmarks",
        speaker="brian", device="iphone", capability="retrieval",
    )

    runner = asyncio.create_task(bench.run_forever())
    try:
        await asyncio.sleep(0.3)
        assert (await ledger.get_brief(brief_id)).state == "running"

        await ledger.amend_brief(
            brief_id, "strix halo backend benchmarks. Vulkan only.", note="spoken"
        )
        for _ in range(200):
            await asyncio.sleep(0.05)
            brief = await ledger.get_brief(brief_id)
            if brief.state == BRIEF_DONE:
                break
        assert brief.state == BRIEF_DONE
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    assert len(llm.prompts) >= 2, "the in-flight generation should have restarted"
    assert "Vulkan only" not in llm.prompts[0]
    assert "Vulkan only" in llm.prompts[-1]

    kinds = [e["kind"] for e in await ledger.events(brief_id)]
    assert {"amendment", "checkpoint", "tool"} <= set(kinds)

    egress = await ledger.egress_for(brief_id)
    assert egress, "a retrieval errand records what left"
    assert not any("search for" in row["query"] for row in egress), \
        "terms leave the property, not the utterance"

    notes = list((tmp_path / "brain" / "notes" / "errands").iterdir())
    assert len(notes) == 1, "one errand, one note, even after an amendment"
    assert "What left the property" in notes[0].read_text()
    await ledger.aclose()


async def test_return_waits_for_a_seam(tmp_path):
    ledger = Ledger(str(tmp_path / "l.db"))
    await ledger.open()
    returner = Returner(ledger, quiet_s=4.0)

    conversation = await ledger.conversation_for("brian")
    brief_id = await ledger.open_brief(
        conversation_id=conversation, statement="compare the backends",
        speaker="brian", device="iphone",
    )
    await ledger.record_result(brief_id, "llama.cpp wins on both.", ttl_s=3600)
    await ledger.close_brief(brief_id, BRIEF_DONE)

    assert not returner.seam(idle=False, quiet_for=99), "never mid-turn"
    assert not returner.seam(idle=True, quiet_for=1.0), "not on the heels of a turn"
    assert returner.seam(idle=True, quiet_for=5.0)

    due = await returner.next_due(conversation)
    assert due is not None
    result, brief = due
    said = returner.phrase(result, brief)
    assert said.startswith("About compare the backends"), \
        "a result twenty minutes later must name its question"

    await returner.delivered(result.id)
    assert await returner.next_due(conversation) is None, "delivered once, never twice"
    await ledger.aclose()


async def test_a_brief_abandoned_by_a_dead_bench_is_requeued(tmp_path):
    """An interrupted errand must not become invisible.

    A `running` brief belongs to a bench that is no longer here. Left alone it is
    not pending, so nothing claims it, and not done, so nothing returns it —
    while the presence has already said "on it". That is precisely the failure
    the strategy singles out: never claim an errand is in hand when it isn't.
    """
    ledger = Ledger(str(tmp_path / "l.db"))
    await ledger.open()
    conversation = await ledger.conversation_for("brian")
    brief_id = await ledger.open_brief(
        conversation_id=conversation, statement="a long errand",
        speaker="brian", device="iphone",
    )
    claimed = await ledger.claim_brief()
    assert claimed is not None and claimed.state == "running"
    assert await ledger.claim_brief() is None, "nothing else can pick it up"

    # ... the bench dies here.
    assert await ledger.requeue_running() == 1
    recovered = await ledger.claim_brief()
    assert recovered is not None and recovered.id == brief_id
    await ledger.aclose()


async def test_the_bench_yields_the_gpu_to_the_presence(tmp_path):
    """The strategy's only non-negotiable number, made mechanical.

    Measured on ser5 2026-08-30: one deep generation makes the presence 2.8x
    slower (first audible word 861ms -> 2451ms). The two llama-server processes
    share one GPU and do not isolate. So the presence takes a floor and the bench
    abandons what it is doing.

    The distinction this asserts is the subtle one: a yield is NOT a completion.
    An abandoned generation must leave the brief unfinished and retry the same
    wording later, never bank the partial answer as the result.
    """
    ledger = Ledger(str(tmp_path / "l.db"))
    await ledger.open()
    llm, search = SlowLlm(), Search(ledger)
    bench = Bench(
        ledger=ledger, llm=llm, search=search, hermes=None,
        exporter=VaultExporter("", ledger), result_ttl_s=3600, poll_s=0.05,
    )

    conversation = await ledger.conversation_for("brian")
    brief_id = await ledger.open_brief(
        conversation_id=conversation, statement="a long errand",
        speaker="brian", device="iphone",
    )

    runner = asyncio.create_task(bench.run_forever())
    try:
        await asyncio.sleep(0.3)
        assert (await ledger.get_brief(brief_id)).state == "running"
        attempts_before = len(llm.prompts)

        # Someone presses the key. This is the `start` frame's job.
        await ledger.take_floor("workstation", hold_s=0.6)
        assert await ledger.floor_taken()

        await asyncio.sleep(0.3)
        assert await ledger.results_for(brief_id) == [], \
            "a yielded generation must NOT be banked as the result"
        assert (await ledger.get_brief(brief_id)).state == "running", \
            "the brief stays claimed while the presence talks"

        # The floor is a deadline, not a lock: it frees itself.
        for _ in range(200):
            await asyncio.sleep(0.05)
            if (await ledger.get_brief(brief_id)).state == BRIEF_DONE:
                break
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    assert (await ledger.get_brief(brief_id)).state == BRIEF_DONE
    assert len(llm.prompts) > attempts_before, "the phase was retried after yielding"

    kinds = [e["kind"] for e in await ledger.events(brief_id)]
    yielded = [
        e for e in await ledger.events(brief_id)
        if e["kind"] == "checkpoint" and "yielded" in e["payload"]
    ]
    assert yielded, f"the yield should be on the record: {kinds}"
    await ledger.aclose()


async def test_the_bench_will_not_start_work_while_someone_is_talking(tmp_path):
    ledger = Ledger(str(tmp_path / "l.db"))
    await ledger.open()
    bench = Bench(
        ledger=ledger, llm=SlowLlm(), search=None, hermes=None,
        exporter=VaultExporter("", ledger), result_ttl_s=3600, poll_s=0.05,
    )
    conversation = await ledger.conversation_for("brian")
    brief_id = await ledger.open_brief(
        conversation_id=conversation, statement="an errand", speaker="brian",
        device="iphone",
    )
    await ledger.take_floor("workstation", hold_s=30)

    runner = asyncio.create_task(bench.run_forever())
    try:
        await asyncio.sleep(0.4)
        assert (await ledger.get_brief(brief_id)).state == "pending", \
            "starting an errand mid-conversation is self-inflicted contention"
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
    await ledger.aclose()
