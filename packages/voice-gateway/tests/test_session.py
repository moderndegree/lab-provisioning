"""The state machine, and the two ordering bugs that presented as "it didn't hear me"."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from conftest import build_app
from helpers import hello, spoken, turn


def test_conversation_resumes_on_a_different_device(tmp_path, vad_model):
    db = str(tmp_path / "ledger.db")

    app, deps = build_app(db, vad_model)
    with TestClient(app) as client, client.websocket_connect("/v1/stream") as ws:
        ready = hello(ws, device="workstation")
        assert ready["resumed"] == 0
        conversation = ready["conversation"]
        assert conversation, "even a first-ever client gets a conversation id"
        turn(ws, "what is the kv ceiling on mini", deps.stt)
        turn(ws, "and on the deep instance", deps.stt)

    # A fresh process — the gateway restarted, or this is simply another device.
    app, deps = build_app(db, vad_model)
    with TestClient(app) as client, client.websocket_connect("/v1/stream") as ws:
        ready = hello(ws, device="iphone")
        assert ready["conversation"] == conversation
        assert ready["resumed"] == 4
        turn(ws, "what did I just ask", deps.stt)

    prompt = deps.llm.seen[-1]
    texts = [m["content"] for m in prompt]
    assert prompt[0]["role"] == "system"
    assert "what is the kv ceiling on mini" in texts
    assert "and on the deep instance" in texts

    rows = sqlite3.connect(db).execute(
        "SELECT device, role, latency_ms FROM turns ORDER BY seq").fetchall()
    assert [r[0] for r in rows] == ["workstation"] * 4 + ["iphone"] * 2
    assert all(r[2] is not None for r in rows if r[1] == "assistant")


def test_back_to_back_turns_are_not_swallowed(tmp_path, vad_model):
    """Speaking again the instant `done` arrives must work. It used not to.

    Everything after `done` awaits — the ledger write — and the next utterance
    can legitimately arrive during that await. Two bugs lived in that window: a
    session still marked SPEAKING routed the new frame to the barge-in check and
    dropped it, and the finishing turn's `finally` reset a session the new turn
    had already claimed. Both looked identical from the outside: the turn simply
    vanished, with no error anywhere.
    """
    db = str(tmp_path / "ledger.db")
    app, deps = build_app(db, vad_model)
    n = 12
    with TestClient(app) as client, client.websocket_connect("/v1/stream") as ws:
        hello(ws)
        for i in range(n):
            frames = turn(ws)  # no pause at all — a key going straight back down
            assert frames[-1]["type"] == "done", frames
            assert any(f["type"] == "final" for f in frames), f"turn {i} was not heard"

    users = [t for r, t in sqlite3.connect(db).execute(
        "SELECT role, text FROM turns ORDER BY seq") if r == "user"]
    assert users == [f"question number {i + 1}" for i in range(n)]


def test_the_presence_refuses_the_world_out_loud(tmp_path, vad_model):
    """With no bench and no network, an errand is declined in the presence's voice.

    `Unreachable` raises if the search client is touched, so this also asserts
    the negative: the fast path did not reach for the network before refusing.
    """
    app, deps = build_app(str(tmp_path / "l.db"), vad_model, presence_network=False)
    with TestClient(app) as client, client.websocket_connect("/v1/stream") as ws:
        hello(ws, device="iphone")

        frames = turn(ws, "search for strix halo benchmarks", deps.stt)
        assert "needs the web" in spoken(frames)[0]
        assert not any(f["type"] == "error" for f in frames), \
            "a denial is re-voiced, never surfaced"

        frames = turn(ws, "have hermes restart open webui", deps.stt)
        assert "hand work off" in spoken(frames)[0]

        frames = turn(ws, "what is the kv ceiling on mini", deps.stt)
        assert spoken(frames), "an ordinary question is still answered"

    assert deps.llm.seen, "the model was used for the plain turn"


def test_a_finished_errand_returns_on_the_same_socket(tmp_path, vad_model):
    """The whole point of one persistent connection.

    An errand finishes while nobody is speaking, and the answer comes back
    through the same voice, naming what was asked. With a socket per turn there
    would be nothing to come back through, and a second socket to listen on
    would be a second thing that can speak.

    The result is written straight into the database from this thread, which is
    exactly how it happens in production: the bench is a separate PROCESS and the
    Ledger file is the only thing between them. If WAL is not doing its job, this
    test is where that shows up.
    """
    import json
    import time
    import uuid

    db = str(tmp_path / "ledger.db")
    app, deps = build_app(
        db, vad_model, return_poll_s=0.1, return_quiet_s=0.1, result_ttl_s=3600
    )

    with TestClient(app) as client, client.websocket_connect("/v1/stream") as ws:
        conversation = hello(ws)["conversation"]

        # An ordinary turn first, with its mode sent per turn rather than in the
        # handshake — the change that let one socket serve both endpointing modes.
        ws.send_text(json.dumps({"type": "start", "mode": "ptt"}))
        assert turn(ws, "what is the kv ceiling on mini", deps.stt)[-1]["type"] == "done"

        brief_id = uuid.uuid4().hex[:16]
        now = time.time()
        with sqlite3.connect(db, timeout=10) as side:
            side.execute(
                "INSERT INTO briefs (id, conversation_id, created_ts, updated_ts, state,"
                " statement, speaker, device, capability, revision)"
                " VALUES (?,?,?,?,'done','compare the backends','brian','iphone','',0)",
                (brief_id, conversation, now, now),
            )
            side.execute(
                "INSERT INTO results (brief_id, ts, text, expires_ts) VALUES (?,?,?,?)",
                (brief_id, now, "llama.cpp wins on both.", now + 3600),
            )

        heard: list[dict] = []
        for _ in range(60):
            message = ws.receive()
            if message.get("text") is None:
                continue
            frame = json.loads(message["text"])
            heard.append(frame)
            if frame["type"] == "done":
                break

        said = [f["text"] for f in heard if f["type"] == "speaking"]
        assert said, f"nothing came back: {heard}"
        assert said[0].startswith("About compare the backends"), said
        assert "llama.cpp wins" in said[0]

    rows = sqlite3.connect(db).execute(
        "SELECT role, text, brief_id FROM turns ORDER BY seq").fetchall()
    returned = [r for r in rows if r[2] == brief_id]
    assert len(returned) == 1 and returned[0][0] == "assistant", \
        "delivered exactly once, and with no phantom question in front of it"


def test_the_locality_probe_tells_its_three_states_apart(tmp_path, vad_model):
    """Blocked, reachable, and "no idea" must not look the same.

    The dangerous one is the third: a timeout is what an unplugged network looks
    like, and reporting it as ok would mean the wall is assumed rather than
    proven — the exact substitution this whole arrangement exists to refuse.
    """
    import asyncio
    import socket
    from dataclasses import replace

    from voice_gateway.app import _locality
    from voice_gateway.config import Config

    base = replace(Config(), locality_probe_timeout=1.0)

    # Reachable: a real listener on loopback stands in for "the rule is not
    # matching", which is what reaching anything at all would mean.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    state, detail = asyncio.run(
        _locality(replace(base, locality_probe_host="127.0.0.1", locality_probe_port=port))
    )
    server.close()
    assert state == "NOT enforced", (state, detail)
    assert "matching nothing" in detail

    # Refused: a port that was open a moment ago and is not any more, so the
    # connection is actively refused. Same shape as an nftables `reject` — an
    # immediate OSError rather than a wait, which is exactly why the rule uses
    # reject and not drop.
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    shut_port = closed.getsockname()[1]
    closed.close()
    state, detail = asyncio.run(
        _locality(replace(base, locality_probe_host="127.0.0.1", locality_probe_port=shut_port))
    )
    assert state == "enforced", (state, detail)
    assert "refused" in detail

    # Silent: a TEST-NET-1 address (RFC 5737) nothing answers for. Times out, and
    # must be reported as proving nothing — this is the case that would otherwise
    # let an unplugged network masquerade as a firewall.
    state, detail = asyncio.run(
        _locality(replace(base, locality_probe_host="192.0.2.1", locality_probe_port=443))
    )
    assert state == "inconclusive", (state, detail)
    assert "proves nothing" in detail
