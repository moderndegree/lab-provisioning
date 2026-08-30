"""The Ledger: the only place state lives, so everything else can be forgotten."""

from __future__ import annotations

import pytest

from voice_gateway.ledger import BRIEF_DONE, Ledger

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def ledger(tmp_path):
    lg = Ledger(str(tmp_path / "ledger.db"))
    await lg.open()
    yield lg
    await lg.aclose()


async def test_conversation_is_stable_per_speaker(ledger):
    first = await ledger.conversation_for("brian")
    assert first == await ledger.conversation_for("brian")


async def test_turns_carry_attribution_and_order(ledger):
    cid = await ledger.conversation_for("brian")
    await ledger.record_turn(conversation_id=cid, speaker="brian", device="workstation",
                             role="user", text="what is the kv ceiling")
    await ledger.record_turn(conversation_id=cid, speaker="brian", device="iphone",
                             role="assistant", text="262144 per slot.", latency_ms=612)
    turns = await ledger.recent_turns(cid, 10)
    assert [t.seq for t in turns] == [1, 2]
    assert [t.device for t in turns] == ["workstation", "iphone"]
    assert all(t.speaker == "brian" for t in turns)


async def test_a_brief_can_only_be_claimed_once(ledger):
    cid = await ledger.conversation_for("brian")
    bid = await ledger.open_brief(conversation_id=cid, statement="compare backends",
                                  speaker="brian", device="iphone", capability="retrieval")
    claimed = await ledger.claim_brief()
    assert claimed is not None and claimed.id == bid
    assert claimed.state == "running"
    assert await ledger.claim_brief() is None


async def test_amendment_bumps_revision_and_keeps_the_old_wording(ledger):
    cid = await ledger.conversation_for("brian")
    bid = await ledger.open_brief(conversation_id=cid, statement="compare backends",
                                  speaker="brian", device="iphone")
    assert await ledger.amend_brief(bid, "compare backends. Vulkan only.") == 1
    events = await ledger.events(bid)
    assert events[0]["kind"] == "amendment"
    assert events[0]["payload"]["was"] == "compare backends"


async def test_a_result_is_delivered_once_and_expiry_is_not_deletion(ledger):
    cid = await ledger.conversation_for("brian")
    bid = await ledger.open_brief(conversation_id=cid, statement="q", speaker="brian",
                                  device="iphone")
    rid = await ledger.record_result(bid, "an answer", ttl_s=3600)
    await ledger.close_brief(bid, BRIEF_DONE)
    assert [r.id for r in await ledger.deliverable(cid)] == [rid]
    await ledger.mark_delivered(rid)
    assert await ledger.deliverable(cid) == []

    stale = await ledger.open_brief(conversation_id=cid, statement="old", speaker="brian",
                                    device="iphone")
    await ledger.record_result(stale, "nobody was around", ttl_s=-1)
    assert await ledger.deliverable(cid) == [], "an expired result is not volunteered"
    assert (await ledger.results_for(stale))[0]["text"], "...but it is still on the record"


async def test_egress_is_attributed(ledger):
    cid = await ledger.conversation_for("brian")
    bid = await ledger.open_brief(conversation_id=cid, statement="strix halo benchmarks",
                                  speaker="brian", device="iphone", capability="retrieval")
    await ledger.record_egress(bid, "http://127.0.0.1:8888/search", "strix halo benchmarks")
    rows = await ledger.egress_for(bid)
    assert len(rows) == 1
    assert "search for" not in rows[0]["query"], "terms, not the transcript"


async def test_a_stalled_brief_is_visible(ledger):
    cid = await ledger.conversation_for("brian")
    await ledger.open_brief(conversation_id=cid, statement="q", speaker="brian",
                            device="iphone")
    assert await ledger.stalled_briefs(older_than_s=300) == []
    assert len(await ledger.stalled_briefs(older_than_s=-1)) == 1
