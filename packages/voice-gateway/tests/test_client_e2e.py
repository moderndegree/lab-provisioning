"""Desktop client against a real Session — the loop a person actually uses.

A scripted gateway can only prove the client speaks the protocol. These spin a
real Session (fake STT/TTS/LLM, real Ledger, real Return) behind uvicorn so a
push-to-talk turn, an errand, and a reconnect go through the same state machine
the box runs. They are the tests that would have caught "I asked it to look
something up and it never came back."
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from client_harness import (
    FakePlayback,
    events_of,
    ptt,
    shutdown,
    start_client,
    start_gateway,
    stop_gateway,
    wait_event,
    wait_idle,
    wait_up,
)
from conftest import build_app


@pytest.fixture
async def live(tmp_path, vad_model):
    db = str(tmp_path / "ledger.db")
    app, deps = build_app(
        db,
        vad_model,
        bench_enabled=True,
        presence_network=False,
        return_poll_s=0.1,
        return_quiet_s=0.1,
        result_ttl_s=3600,
    )
    server, task, url = await start_gateway(app)
    try:
        yield {"url": url, "deps": deps, "db": db}
    finally:
        await stop_gateway(server, task)


async def _connect(live, **kw):
    play = FakePlayback()
    client, stop, task = start_client(live["url"], play, **kw)
    await wait_up(client)
    return play, client, stop, task


async def _turn(client, live, said: str) -> None:
    before = len(events_of(client, "done"))
    live["deps"].stt.queue.append(said)
    await ptt(client)
    await wait_event(client, "done", min_count=before + 1)
    await wait_idle(client)


# ─── ordinary conversation ───────────────────────────────────────────────────

async def test_a_spoken_turn_comes_back_as_text_and_audio(live):
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "what is the kv ceiling on mini")
        spoken = events_of(client, "speaking")
        assert spoken, "presence never spoke"
        assert play.played, "TTS bytes never reached the client"
        assert play.flushes == 0
        finals = events_of(client, "final")
        assert finals and "kv ceiling" in finals[0]["text"]
    finally:
        await shutdown(client, stop, task)


async def test_back_to_back_ptt_turns_are_not_swallowed(live):
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "what is the kv ceiling on mini")
        await _turn(client, live, "and on the deep instance")
        finals = [e["text"] for e in events_of(client, "final")]
        assert finals[-2:] == [
            "what is the kv ceiling on mini",
            "and on the deep instance",
        ]
        assert len(events_of(client, "done")) >= 2
    finally:
        await shutdown(client, stop, task)


async def test_attribution_is_the_device_and_speaker_from_hello(live):
    play, client, stop, task = await _connect(
        live, device="workstation", speaker="brian"
    )
    try:
        await _turn(client, live, "hello")
        rows = sqlite3.connect(live["db"]).execute(
            "SELECT device, speaker, role FROM turns ORDER BY seq"
        ).fetchall()
        assert rows
        assert {r[0] for r in rows} == {"workstation"}
        assert {r[1] for r in rows} == {"brian"}
    finally:
        await shutdown(client, stop, task)


# ─── errands ─────────────────────────────────────────────────────────────────

async def test_search_dispatches_an_errand_instead_of_answering(live):
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "search for strix halo benchmarks")
        briefs = events_of(client, "brief")
        assert briefs, "presence answered instead of dispatching"
        assert "strix halo" in briefs[0]["statement"]
        spoken = events_of(client, "speaking")
        assert any("on it" in s["text"].lower() for s in spoken)
        states = sqlite3.connect(live["db"]).execute(
            "SELECT state, capability FROM briefs"
        ).fetchall()
        assert states and states[0][1] == "retrieval"
    finally:
        await shutdown(client, stop, task)


async def test_a_finished_errand_returns_without_another_keypress(live):
    """The bug: 'I'll come back to you' and then silence.

    Bench writes the result into the Ledger. Return must volunteer it on the
    same socket, at a seam, without the human polling by speaking again.
    """
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "search for strix halo benchmarks")
        conversation = client.ready["conversation"]
        brief_id = events_of(client, "brief")[0]["id"]

        now = time.time()
        with sqlite3.connect(live["db"], timeout=10) as side:
            side.execute(
                "INSERT INTO results (brief_id, ts, text, expires_ts) VALUES (?,?,?,?)",
                (brief_id, now, "llama.cpp wins on both.", now + 3600),
            )
            side.execute(
                "UPDATE briefs SET state='done', updated_ts=? WHERE id=?",
                (now, brief_id),
            )
            side.commit()

        await wait_event(client, "notice", timeout=3)
        spoken = events_of(client, "speaking")
        returned = [s for s in spoken if s["text"].startswith("About ")]
        assert returned, f"errand never spoken back: {spoken}"
        assert "llama.cpp wins" in returned[0]["text"]
        assert play.played, "returning result had no audio"
        assert client.idle.is_set()

        delivered = sqlite3.connect(live["db"]).execute(
            "SELECT delivered_ts FROM results WHERE brief_id=?", (brief_id,)
        ).fetchone()[0]
        assert delivered is not None
        # conversation still the same — not a new session
        assert client.ready["conversation"] == conversation
    finally:
        await shutdown(client, stop, task)


async def test_trailing_pcm_after_done_does_not_block_return(live):
    """In-flight mic frames used to open a phantom LISTENING turn.

    The client now drops them, and the session ignores unarmed PTT audio.
    Together they keep the seam open so Return can fire.
    """
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "search for the latest 27 billion parameter model")
        # Stuff the mic the way the hardware callback does between turns.
        for _ in range(20):
            try:
                client.mic.put_nowait(b"\x07" * 640)
            except Exception:
                break
        await asyncio.sleep(0.15)

        brief_id = events_of(client, "brief")[0]["id"]
        now = time.time()
        with sqlite3.connect(live["db"], timeout=10) as side:
            side.execute(
                "INSERT INTO results (brief_id, ts, text, expires_ts) VALUES (?,?,?,?)",
                (brief_id, now, "Qwen3.8-27B is the one.", now + 3600),
            )
            side.execute(
                "UPDATE briefs SET state='done', updated_ts=? WHERE id=?",
                (now, brief_id),
            )
            side.commit()

        await wait_event(client, "notice", timeout=3)
        returned = [
            s for s in events_of(client, "speaking") if "Qwen3.8" in s["text"]
        ]
        assert returned, "trailing PCM stranded the result"
    finally:
        await shutdown(client, stop, task)


# ─── reconnect ───────────────────────────────────────────────────────────────

async def test_reconnect_resumes_the_same_conversation(live):
    play, client, stop, task = await _connect(live, device="workstation")
    try:
        await _turn(client, live, "what is the kv ceiling on mini")
        conversation = client.ready["conversation"]
    finally:
        await shutdown(client, stop, task)

    play2, client2, stop2, task2 = await _connect(live, device="workstation")
    try:
        assert client2.ready["conversation"] == conversation
        assert client2.ready["resumed"] >= 2
        await _turn(client2, live, "what did I just ask")
        assert events_of(client2, "speaking")
    finally:
        await shutdown(client2, stop2, task2)


async def test_reconnect_delivers_a_result_that_landed_while_gone(live):
    play, client, stop, task = await _connect(live)
    try:
        await _turn(client, live, "search for strix halo benchmarks")
        conversation = client.ready["conversation"]
        brief_id = events_of(client, "brief")[0]["id"]
    finally:
        await shutdown(client, stop, task)

    now = time.time()
    with sqlite3.connect(live["db"], timeout=10) as side:
        side.execute(
            "INSERT INTO results (brief_id, ts, text, expires_ts) VALUES (?,?,?,?)",
            (brief_id, now, "llama.cpp wins on both.", now + 3600),
        )
        side.execute(
            "UPDATE briefs SET state='done', updated_ts=? WHERE id=?",
            (now, brief_id),
        )
        side.commit()

    play2, client2, stop2, task2 = await _connect(live, device="workstation")
    try:
        assert client2.ready["conversation"] == conversation
        assert client2.ready["waiting"] >= 1
        await wait_event(client2, "notice", timeout=3)
        returned = [
            s for s in events_of(client2, "speaking") if "llama.cpp" in s["text"]
        ]
        assert returned, "waiting result was not volunteered on reconnect"
    finally:
        await shutdown(client2, stop2, task2)
