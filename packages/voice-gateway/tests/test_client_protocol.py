"""Desktop client against a scripted gateway — the wire protocol, from this side.

These do not prove the loop sounds good. They prove the client speaks the
contract in `voice_gateway/protocol.py` and does not throw away audio, swallow
a returning errand, or keep talking after `done`. Every one of those presented
to a person as "it didn't work" with no error anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from voice_client import is_interrupt

from client_harness import (
    FakeLab,
    FakePlayback,
    events_of,
    inbox_types,
    keep_feeding,
    ptt,
    shutdown,
    start_client,
    wait_event,
    wait_idle,
    wait_up,
)


@pytest.fixture
async def lab():
    async with FakeLab() as fake:
        yield fake


async def _boot(lab, **client_kw):
    play = FakePlayback()
    client, stop, task = start_client(lab.url, play, **client_kw)
    await wait_up(client)
    await lab.connected.wait()
    return play, client, stop, task


def test_ctrl_c_is_an_interrupt_even_when_delivered_as_etx():
    """pynput often reports ctrl+c as ETX, not the letter c."""
    assert is_interrupt({"ctrl"}, "c")
    assert is_interrupt({"ctrl"}, "\x03")
    assert is_interrupt({"ctrl", "alt"}, "c")
    assert not is_interrupt(set(), "c")
    assert not is_interrupt({"ctrl"}, "space")
    assert not is_interrupt({"alt"}, "c")


# ─── handshake ───────────────────────────────────────────────────────────────

async def test_hello_names_the_device_and_the_rate(lab):
    play, client, stop, task = await _boot(lab, device="workstation", speaker="brian")
    try:
        assert lab.hello == {
            "type": "hello",
            "device": "workstation",
            "mode": "ptt",
            "sample_rate": 16000,
            "speaker": "brian",
        }
        assert client.ready["conversation"] == "conv-test"
        assert client.ready["resumed"] == 0
    finally:
        await shutdown(client, stop, task)


async def test_hello_omits_speaker_when_blank(lab):
    play, client, stop, task = await _boot(lab, speaker="")
    try:
        assert "speaker" not in lab.hello
    finally:
        await shutdown(client, stop, task)


async def test_ready_with_waiting_results_is_surfaced(lab):
    """A reconnect that has something to say must not look like a cold start."""
    async with FakeLab(resumed=12, working=1, waiting=2) as busy:
        play, client, stop, task = await _boot(busy)
        try:
            assert client.ready["resumed"] == 12
            assert client.ready["working"] == 1
            assert client.ready["waiting"] == 2
        finally:
            await shutdown(client, stop, task)


# ─── push-to-talk ────────────────────────────────────────────────────────────

async def test_ptt_sends_start_pcm_then_end(lab):
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client, frames=4)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        kinds = inbox_types(lab)
        start_at = kinds.index("start")
        end_at = kinds.index("end")
        pcm = kinds[start_at + 1 : end_at]
        assert kinds[start_at] == "start"
        assert json.loads(lab.inbox[start_at])["mode"] == "ptt"
        assert pcm, "no PCM between start and end"
        assert all(k == "pcm" for k in pcm)
        assert kinds[end_at] == "end"
        # Turn is still open (waiting for done); finish it so shutdown is clean.
        await lab.send_json({"type": "done"})
        await wait_idle(client)
    finally:
        await shutdown(client, stop, task)


async def test_wake_word_turn_sends_vad_mode(lab):
    play, client, stop, task = await _boot(lab)
    try:
        client.trigger.put("vad")
        await lab.wait_inbox(lambda inbox: "start" in inbox_types(lab))
        starts = [
            json.loads(item)
            for item in lab.inbox
            if isinstance(item, str) and json.loads(item).get("type") == "start"
        ]
        assert starts[-1]["mode"] == "vad"
        await lab.send_json({"type": "done"})
        await wait_idle(client)
    finally:
        await shutdown(client, stop, task)


# ─── playback ────────────────────────────────────────────────────────────────

async def test_audio_is_drained_not_flushed_on_done(lab):
    """The bug that played a blip and showed the full text.

    Gateway synthesises faster than realtime, then sends `done`. Flushing the
    jitter buffer at that moment throws the rest of the sentence away.
    """
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        pcm = b"\x11\x22" * 500
        await lab.send_json({"type": "speaking", "seq": 1, "text": "Hello there."})
        await lab.send_pcm(pcm)
        await lab.send_json({"type": "done"})
        await wait_idle(client)
        assert play.flushes == 0
        assert play.played == pcm
        assert play.queued_bytes() == 0
        assert events_of(client, "speaking")[0]["text"] == "Hello there."
        assert events_of(client, "audio")[0]["bytes"] == len(pcm)
    finally:
        await shutdown(client, stop, task)


async def test_cancelled_flushes_unplayed_audio(lab):
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "speaking", "seq": 1, "text": "Long answer"})
        await lab.send_pcm(b"\x33" * 200)
        await lab.send_json({"type": "cancelled"})
        await wait_idle(client)
        assert play.flushes >= 1
        assert play.played == b""
        assert play.queued_bytes() == 0
    finally:
        await shutdown(client, stop, task)


async def test_partial_and_final_are_recorded(lab):
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "partial", "text": "look it"})
        await lab.send_json({"type": "final", "text": "look it up"})
        await lab.send_json({"type": "speaking", "seq": 1, "text": "On it."})
        await lab.send_json({"type": "done"})
        await wait_idle(client)
        assert events_of(client, "partial")[0]["text"] == "look it"
        assert events_of(client, "final")[0]["text"] == "look it up"
    finally:
        await shutdown(client, stop, task)


# ─── after done ──────────────────────────────────────────────────────────────

async def test_no_pcm_is_sent_after_done_even_if_the_mic_is_full(lab):
    """Trailing PCM after `done` is what stranded returning errands.

    The real mic callback never stops. One frame pulled from the queue after
    `done` landed used to go out, the gateway opened a new utterance, and
    Return never saw idle.
    """
    play, client, stop, task = await _boot(lab)
    feeder = None
    try:
        await ptt(client)
        feeder = keep_feeding(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "done"})
        await wait_idle(client)
        n_after_done = len(lab.inbox)
        await asyncio.sleep(0.2)
        assert lab.inbox[n_after_done:] == [], (
            f"client kept sending after done: {inbox_types(lab)}"
        )
    finally:
        if feeder is not None:
            feeder.cancel()
        await shutdown(client, stop, task)


async def test_error_frees_the_floor(lab):
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "error", "message": "Speech recognition is unavailable."})
        await wait_idle(client)
        assert events_of(client, "error")
    finally:
        await shutdown(client, stop, task)


# ─── errands come back on the same socket ────────────────────────────────────

async def test_a_finished_errand_is_heard_without_another_ptt(lab):
    """The whole point of one persistent connection.

    After "On it", the client sits on the trigger queue. receive_forever must
    still be reading, or a result twenty seconds later has nowhere to go.
    """
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "brief", "id": "abc", "statement": "strix halo"})
        await lab.send_json({"type": "working", "n": 1})
        await lab.send_json({"type": "speaking", "seq": 1, "text": "On it. I'll come back to you."})
        await lab.send_pcm(b"\x44" * 40)
        await lab.send_json({"type": "done"})
        await wait_idle(client)

        # No second keypress. The result arrives on the socket that is still open.
        await lab.send_json({"type": "notice", "text": "llama.cpp wins on both."})
        await lab.send_json({
            "type": "speaking", "seq": 1,
            "text": "About strix halo — llama.cpp wins on both.",
        })
        result_pcm = b"\x55" * 80
        await lab.send_pcm(result_pcm)
        await lab.send_json({"type": "done"})
        await wait_event(client, "notice")
        spoken = events_of(client, "speaking")
        assert spoken[-1]["text"].startswith("About strix halo")
        assert result_pcm in play.heard
        # Still waiting for a trigger — not stuck in a turn.
        assert client.idle.is_set()
    finally:
        await shutdown(client, stop, task)


async def test_brief_and_working_frames_are_recorded(lab):
    play, client, stop, task = await _boot(lab)
    try:
        await ptt(client)
        await lab.wait_inbox(lambda inbox: "end" in inbox_types(lab))
        await lab.send_json({"type": "brief", "id": "b1", "statement": "the latest 27b"})
        await lab.send_json({"type": "working", "n": 1})
        await lab.send_json({"type": "done"})
        await wait_idle(client)
        assert events_of(client, "brief")[0]["statement"] == "the latest 27b"
        assert events_of(client, "working")[0]["n"] == 1
    finally:
        await shutdown(client, stop, task)


# ─── reconnect ───────────────────────────────────────────────────────────────

async def test_a_dropped_socket_reconnects_without_a_keypress():
    play = FakePlayback()
    async with FakeLab() as lab:
        from voice_client import Client

        client = Client(lab.url, play)
        stop = asyncio.Event()
        forever = asyncio.create_task(client.run_forever(stop, retry_s=0.05))
        await wait_up(client)
        await lab.connected.wait()
        try:
            client.up.clear()
            await lab.close_socket()
            await asyncio.wait_for(client.up.wait(), timeout=2)
            assert client.drops >= 1
            assert client.ready is not None
        finally:
            stop.set()
            forever.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(forever, timeout=2)
