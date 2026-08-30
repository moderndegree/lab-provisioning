"""The Ledger — the only place state lives.

Durable conversation, briefs, results and egress, each attributed to a speaker
and a device. Everything else in this package is allowed to be forgotten on a
restart; this is not.

WHY SQLITE, AND WHY HERE. The gateway and the bench are two processes that must
see the same briefs, and the handoff between them has to survive either one
restarting. A file both can open under WAL is the smallest thing that does that.
It lives under `{data_mount}/services/voice`, which is already inside restic's
`/data` — no new backup path to remember.

WHY A THREAD, NOT `aiosqlite`. sqlite3 is in the standard library and these
writes are a few hundred bytes each; a dependency would buy nothing. Every
statement runs on ONE dedicated worker thread, which serialises this process's
access without a lock and keeps the event loop free. Cross-process concurrency is
WAL's problem, with `busy_timeout` for the rare overlap.

ATTRIBUTION IS NOT OPTIONAL AND NOT DEFERRABLE. `speaker` is "brian" on every row
today and there is exactly one user, so every column here could be dropped and
nothing would behave differently. They exist anyway: isolation can be built later
on top of attributed history, and attribution cannot be recovered retroactively.
The same reasoning keeps `conversations` a table rather than an implicit
singleton — several people in one room is a different product from one assistant
with several users, and neither reading is foreclosed here.

`egress` is the table that makes the locality claim testable rather than
asserted. Every outbound query, with the named brief that caused it. Nothing but
the bench may write it, and `roles/voice/tasks/verify.yml` reads it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# States a brief moves through. Deliberately few: anything richer wants a state
# machine, and a brief that needs one is a brief that should have been two.
BRIEF_PENDING = "pending"
BRIEF_RUNNING = "running"
BRIEF_DONE = "done"
BRIEF_FAILED = "failed"
BRIEF_CANCELLED = "cancelled"

# brief_events.kind
EVENT_AMENDMENT = "amendment"
EVENT_CHECKPOINT = "checkpoint"
EVENT_TOOL = "tool"
EVENT_NOTE = "note"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    speaker     TEXT NOT NULL,
    created_ts  REAL NOT NULL,
    last_ts     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    seq             INTEGER NOT NULL,
    ts              REAL NOT NULL,
    speaker         TEXT NOT NULL,
    device          TEXT NOT NULL,
    role            TEXT NOT NULL,   -- user | assistant
    text            TEXT NOT NULL,
    latency_ms      INTEGER,
    brief_id        TEXT REFERENCES briefs(id)
);
CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns(conversation_id, seq);

-- The Brief: the mutable statement of what is wanted. `statement` is the
-- CURRENT wording; the history of how it got there is in brief_events, which is
-- what lets an in-flight errand be steered rather than replaced.
CREATE TABLE IF NOT EXISTS briefs (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    created_ts      REAL NOT NULL,
    updated_ts      REAL NOT NULL,
    state           TEXT NOT NULL,
    statement       TEXT NOT NULL,
    speaker         TEXT NOT NULL,
    device          TEXT NOT NULL,
    -- Which staff entrance this errand needs, as Judgment saw it: retrieval,
    -- delegation, or empty for "the deep model on its own". Recorded rather than
    -- re-derived so the bench does not have to re-parse an utterance it never
    -- heard, and so an amended statement cannot silently change what an errand
    -- is allowed to reach.
    capability      TEXT NOT NULL DEFAULT '',
    -- Bumped on every amendment. The bench compares the revision it started a
    -- step with against the current one; that comparison IS mid-flight steering.
    revision        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS briefs_by_state ON briefs(state, created_ts);

CREATE TABLE IF NOT EXISTS brief_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id  TEXT NOT NULL REFERENCES briefs(id),
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    payload   TEXT NOT NULL   -- JSON
);
CREATE INDEX IF NOT EXISTS brief_events_by_brief ON brief_events(brief_id, id);

CREATE TABLE IF NOT EXISTS results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id     TEXT NOT NULL REFERENCES briefs(id),
    ts           REAL NOT NULL,
    text         TEXT NOT NULL,
    delivered_ts REAL,
    expires_ts   REAL NOT NULL,
    exported_ts  REAL
);
CREATE INDEX IF NOT EXISTS results_undelivered ON results(delivered_ts, expires_ts);

-- THE FLOOR. One row, upserted by the presence, read by the bench.
--
-- Measured on ser5 2026-08-30: a single deep generation on mini's :8091 makes
-- the presence on :8090 2.8x slower — first audible word 861ms -> 2451ms, and
-- model-to-first-sentence 498ms -> 2041ms. Two llama-server processes sharing
-- one GPU do not isolate, and Nice/CPUWeight on the bench unit are decorative
-- because the contention is not CPU.
--
-- So the bench yields. `taken_until` is a deadline, not a lock: the presence
-- pushes it forward while it holds the floor and never has to release it, so a
-- crashed gateway frees the bench on its own after a few seconds. Nothing here
-- blocks, and nothing here can deadlock.
CREATE TABLE IF NOT EXISTS floor (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    taken_until REAL NOT NULL,
    device      TEXT NOT NULL
);

-- Every packet that left the property, and the brief that justified it. A row
-- here with no brief_id is a bug in the bench, not a permitted case.
CREATE TABLE IF NOT EXISTS egress (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id TEXT REFERENCES briefs(id),
    ts       REAL NOT NULL,
    endpoint TEXT NOT NULL,
    query    TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Turn:
    seq: int
    ts: float
    speaker: str
    device: str
    role: str
    text: str


@dataclass(frozen=True)
class Brief:
    id: str
    conversation_id: str
    created_ts: float
    updated_ts: float
    state: str
    statement: str
    speaker: str
    device: str
    capability: str
    revision: int


@dataclass(frozen=True)
class Result:
    id: int
    brief_id: str
    ts: float
    text: str
    delivered_ts: float | None
    expires_ts: float


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Ledger:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        # One worker, always. More would only reintroduce the write contention
        # that a single writer thread exists to avoid.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ledger")

    # ─── lifecycle ───────────────────────────────────────────────────────────
    async def open(self) -> None:
        await self._run(self._open_sync)

    def _open_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL is the whole reason two processes can share this file: a reader
        # (the bench) never blocks the writer (the presence), which matters
        # because the presence's writes sit on the fast path.
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL rather than FULL: a crash may lose the last few turns, and
        # fsyncing every turn would put disk latency on the conversation.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # Every migration so far has been ADDITIVE — a new table or a column
            # with a default — and `executescript` above has already applied it
            # via CREATE TABLE IF NOT EXISTS. So stamping the version is the
            # whole migration. The day one is not additive, this branch has to
            # grow real steps, and the assert below is what will say so.
            if version:
                log.info("ledger schema v%s -> v%s (additive)", version, SCHEMA_VERSION)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif version > SCHEMA_VERSION:
            log.warning(
                "ledger schema is v%s but this build expects v%s — it was written "
                "by a NEWER voice-gateway. Running anyway; columns it added are "
                "invisible here.",
                version, SCHEMA_VERSION,
            )
        conn.commit()
        self._conn = conn
        log.info("ledger open at %s (schema v%s)", self._path, SCHEMA_VERSION)

    async def aclose(self) -> None:
        await self._run(self._close_sync)
        self._pool.shutdown(wait=False)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def health(self) -> tuple[bool, str]:
        """Whether the file is open and writable. `/v1/status` reports this."""
        try:
            counts = await self._run(self._health_sync)
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return False, f"{self._path}: {exc}"
        return True, f"{self._path} ({counts})"

    def _health_sync(self) -> str:
        c = self._db()
        turns = c.execute("SELECT count(*) FROM turns").fetchone()[0]
        briefs = c.execute("SELECT count(*) FROM briefs").fetchone()[0]
        return f"{turns} turns, {briefs} briefs"

    # ─── conversations (the Doorman's substrate) ─────────────────────────────
    async def conversation_for(self, speaker: str) -> str:
        """The one live conversation for this speaker, created if absent.

        Deliberately NOT keyed on device. A conversation follows the person
        across every piece of glass on the tailnet; the device is recorded per
        turn instead, so "where was I when I said that" survives without
        fragmenting the thread.
        """
        return await self._run(self._conversation_for_sync, speaker)

    def _conversation_for_sync(self, speaker: str) -> str:
        c = self._db()
        row = c.execute(
            "SELECT id FROM conversations WHERE speaker=? ORDER BY last_ts DESC LIMIT 1",
            (speaker,),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        now = time.time()
        cid = _new_id()
        c.execute(
            "INSERT INTO conversations (id, speaker, created_ts, last_ts) VALUES (?,?,?,?)",
            (cid, speaker, now, now),
        )
        c.commit()
        log.info("new conversation %s for speaker=%s", cid, speaker)
        return cid

    # ─── turns ───────────────────────────────────────────────────────────────
    async def recent_turns(self, conversation_id: str, limit: int) -> list[Turn]:
        """The tail of the conversation, oldest first.

        `limit` counts ROWS, not exchanges — the caller asks for
        `llm_history_turns * 2` for the same reason `Session._remember` keeps
        that many: the system prompt is the prefix-cache key on mini and every
        extra turn pushes the cacheable prefix further from the front.
        """
        return await self._run(self._recent_turns_sync, conversation_id, limit)

    def _recent_turns_sync(self, conversation_id: str, limit: int) -> list[Turn]:
        rows = self._db().execute(
            "SELECT seq, ts, speaker, device, role, text FROM turns "
            "WHERE conversation_id=? ORDER BY seq DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [
            Turn(
                seq=r["seq"],
                ts=r["ts"],
                speaker=r["speaker"],
                device=r["device"],
                role=r["role"],
                text=r["text"],
            )
            for r in reversed(rows)
        ]

    async def record_exchange(
        self,
        *,
        conversation_id: str,
        speaker: str,
        device: str,
        user: str,
        assistant: str,
        latency_ms: int | None = None,
        brief_id: str | None = None,
    ) -> None:
        """Write one exchange — both turns — in a single transaction.

        ATOMIC BECAUSE A HALF-WRITTEN EXCHANGE IS WORSE THAN NONE. A user turn
        with no answer after it reads back, on the next connect, as a question
        the presence ignored; the model then sees its own silence as precedent.
        Writing them as two awaited calls left exactly that gap — and the gap was
        real, not theoretical: a client hanging up between them raced the
        Ledger's own close and lost the second row.

        `user` may be empty, for a result returning with no question in front of
        it. Nothing is written for the empty half.
        """
        await self._run(
            self._record_exchange_sync,
            conversation_id, speaker, device, user, assistant, latency_ms, brief_id,
        )

    def _record_exchange_sync(
        self,
        conversation_id: str,
        speaker: str,
        device: str,
        user: str,
        assistant: str,
        latency_ms: int | None,
        brief_id: str | None,
    ) -> None:
        c = self._db()
        now = time.time()
        seq = c.execute(
            "SELECT coalesce(max(seq), 0) + 1 FROM turns WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        rows = []
        if user:
            rows.append((conversation_id, seq, now, speaker, device, "user", user,
                         None, brief_id))
            seq += 1
        rows.append((conversation_id, seq, now, speaker, device, "assistant", assistant,
                     latency_ms, brief_id))
        c.executemany(
            "INSERT INTO turns (conversation_id, seq, ts, speaker, device, role, text,"
            " latency_ms, brief_id) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        c.execute("UPDATE conversations SET last_ts=? WHERE id=?", (now, conversation_id))
        c.commit()

    async def record_turn(
        self,
        *,
        conversation_id: str,
        speaker: str,
        device: str,
        role: str,
        text: str,
        latency_ms: int | None = None,
        brief_id: str | None = None,
    ) -> int:
        return await self._run(
            self._record_turn_sync,
            conversation_id,
            speaker,
            device,
            role,
            text,
            latency_ms,
            brief_id,
        )

    def _record_turn_sync(
        self,
        conversation_id: str,
        speaker: str,
        device: str,
        role: str,
        text: str,
        latency_ms: int | None,
        brief_id: str | None,
    ) -> int:
        c = self._db()
        now = time.time()
        # seq is allocated here rather than by the caller so two devices talking
        # into the same conversation cannot collide on it.
        seq = c.execute(
            "SELECT coalesce(max(seq), 0) + 1 FROM turns WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        cur = c.execute(
            "INSERT INTO turns (conversation_id, seq, ts, speaker, device, role, text,"
            " latency_ms, brief_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (conversation_id, seq, now, speaker, device, role, text, latency_ms, brief_id),
        )
        c.execute("UPDATE conversations SET last_ts=? WHERE id=?", (now, conversation_id))
        c.commit()
        return int(cur.lastrowid or 0)

    # ─── briefs ──────────────────────────────────────────────────────────────
    async def open_brief(
        self,
        *,
        conversation_id: str,
        statement: str,
        speaker: str,
        device: str,
        capability: str = "",
    ) -> str:
        """Write a brief and return its id. THIS WRITE IS THE HANDOFF.

        There is no in-process queue between the presence and the bench. The row
        is the dispatch, so a gateway restart between "on it" and the bench
        picking it up loses nothing.
        """
        return await self._run(
            self._open_brief_sync, conversation_id, statement, speaker, device, capability
        )

    def _open_brief_sync(
        self,
        conversation_id: str,
        statement: str,
        speaker: str,
        device: str,
        capability: str,
    ) -> str:
        c = self._db()
        now = time.time()
        bid = _new_id()
        c.execute(
            "INSERT INTO briefs (id, conversation_id, created_ts, updated_ts, state,"
            " statement, speaker, device, capability, revision)"
            " VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                bid, conversation_id, now, now, BRIEF_PENDING, statement, speaker,
                device, capability,
            ),
        )
        c.commit()
        log.info("brief %s opened: %s", bid, statement[:120])
        return bid

    async def get_brief(self, brief_id: str) -> Brief | None:
        return await self._run(self._get_brief_sync, brief_id)

    def _get_brief_sync(self, brief_id: str) -> Brief | None:
        row = self._db().execute("SELECT * FROM briefs WHERE id=?", (brief_id,)).fetchone()
        return _brief(row) if row else None

    async def live_briefs(self, conversation_id: str) -> list[Brief]:
        """Briefs still in flight — what "what are you working on" answers from."""
        return await self._run(self._live_briefs_sync, conversation_id)

    def _live_briefs_sync(self, conversation_id: str) -> list[Brief]:
        rows = self._db().execute(
            "SELECT * FROM briefs WHERE conversation_id=? AND state IN (?,?)"
            " ORDER BY created_ts",
            (conversation_id, BRIEF_PENDING, BRIEF_RUNNING),
        ).fetchall()
        return [_brief(r) for r in rows]

    async def amend_brief(self, brief_id: str, statement: str, *, note: str = "") -> int:
        """Replace the statement and bump the revision. Returns the new revision.

        The old wording is not lost — it goes into brief_events, so an errand
        that produced a surprising result can be read back as the sequence of
        things that were actually asked for.
        """
        return await self._run(self._amend_brief_sync, brief_id, statement, note)

    def _amend_brief_sync(self, brief_id: str, statement: str, note: str) -> int:
        c = self._db()
        now = time.time()
        row = c.execute(
            "SELECT statement, revision FROM briefs WHERE id=?", (brief_id,)
        ).fetchone()
        if row is None:
            raise KeyError(brief_id)
        revision = int(row["revision"]) + 1
        c.execute(
            "UPDATE briefs SET statement=?, revision=?, updated_ts=? WHERE id=?",
            (statement, revision, now, brief_id),
        )
        c.execute(
            "INSERT INTO brief_events (brief_id, ts, kind, payload) VALUES (?,?,?,?)",
            (
                brief_id,
                now,
                EVENT_AMENDMENT,
                json.dumps({"was": row["statement"], "now": statement, "note": note}),
            ),
        )
        c.commit()
        log.info("brief %s amended to r%s: %s", brief_id, revision, statement[:120])
        return revision

    async def claim_brief(self) -> Brief | None:
        """Atomically take the oldest pending brief. Bench-only.

        The UPDATE ... WHERE state='pending' is the claim: two bench processes
        racing here cannot both win, because SQLite serialises the write.
        """
        return await self._run(self._claim_brief_sync)

    def _claim_brief_sync(self) -> Brief | None:
        c = self._db()
        row = c.execute(
            "SELECT * FROM briefs WHERE state=? ORDER BY created_ts LIMIT 1",
            (BRIEF_PENDING,),
        ).fetchone()
        if row is None:
            return None
        changed = c.execute(
            "UPDATE briefs SET state=?, updated_ts=? WHERE id=? AND state=?",
            (BRIEF_RUNNING, time.time(), row["id"], BRIEF_PENDING),
        ).rowcount
        c.commit()
        if not changed:
            return None
        return _brief(c.execute("SELECT * FROM briefs WHERE id=?", (row["id"],)).fetchone())

    async def stalled_briefs(self, *, older_than_s: float) -> list[Brief]:
        """Pending briefs nothing has claimed. The bench's liveness, seen sideways.

        The gateway cannot inspect a separate unit, but it can notice that work
        it promised is not being done — which is the failure that matters and the
        one that otherwise looks completely healthy from every angle.
        """
        return await self._run(self._stalled_briefs_sync, older_than_s)

    def _stalled_briefs_sync(self, older_than_s: float) -> list[Brief]:
        rows = self._db().execute(
            "SELECT * FROM briefs WHERE state=? AND created_ts < ? ORDER BY created_ts",
            (BRIEF_PENDING, time.time() - older_than_s),
        ).fetchall()
        return [_brief(r) for r in rows]

    async def requeue_running(self) -> int:
        """Put every `running` brief back to `pending`. Bench startup only.

        A brief is only `running` while a bench holds it, and there is one bench.
        So anything found in that state at startup was abandoned — and an
        abandoned brief is invisible to everything: not pending so nothing claims
        it, not done so nothing returns it, while the presence has already
        promised an answer.
        """
        return await self._run(self._requeue_running_sync)

    def _requeue_running_sync(self) -> int:
        c = self._db()
        n = c.execute(
            "UPDATE briefs SET state=?, updated_ts=? WHERE state=?",
            (BRIEF_PENDING, time.time(), BRIEF_RUNNING),
        ).rowcount
        c.commit()
        return int(n)

    async def close_brief(self, brief_id: str, state: str) -> None:
        await self._run(self._close_brief_sync, brief_id, state)

    def _close_brief_sync(self, brief_id: str, state: str) -> None:
        c = self._db()
        c.execute(
            "UPDATE briefs SET state=?, updated_ts=? WHERE id=?",
            (state, time.time(), brief_id),
        )
        c.commit()

    async def add_event(self, brief_id: str, kind: str, payload: dict[str, Any]) -> None:
        await self._run(self._add_event_sync, brief_id, kind, payload)

    def _add_event_sync(self, brief_id: str, kind: str, payload: dict[str, Any]) -> None:
        c = self._db()
        c.execute(
            "INSERT INTO brief_events (brief_id, ts, kind, payload) VALUES (?,?,?,?)",
            (brief_id, time.time(), kind, json.dumps(payload)),
        )
        c.commit()

    async def events(self, brief_id: str) -> list[dict[str, Any]]:
        return await self._run(self._events_sync, brief_id)

    def _events_sync(self, brief_id: str) -> list[dict[str, Any]]:
        rows = self._db().execute(
            "SELECT ts, kind, payload FROM brief_events WHERE brief_id=? ORDER BY id",
            (brief_id,),
        ).fetchall()
        return [
            {"ts": r["ts"], "kind": r["kind"], "payload": json.loads(r["payload"])}
            for r in rows
        ]

    # ─── results (what Return delivers) ──────────────────────────────────────
    async def record_result(self, brief_id: str, text: str, *, ttl_s: float) -> int:
        return await self._run(self._record_result_sync, brief_id, text, ttl_s)

    def _record_result_sync(self, brief_id: str, text: str, ttl_s: float) -> int:
        c = self._db()
        now = time.time()
        cur = c.execute(
            "INSERT INTO results (brief_id, ts, text, expires_ts) VALUES (?,?,?,?)",
            (brief_id, now, text, now + ttl_s),
        )
        c.commit()
        return int(cur.lastrowid or 0)

    async def deliverable(self, conversation_id: str) -> list[Result]:
        """Undelivered, unexpired results for this conversation, oldest first."""
        return await self._run(self._deliverable_sync, conversation_id)

    def _deliverable_sync(self, conversation_id: str) -> list[Result]:
        rows = self._db().execute(
            "SELECT r.* FROM results r JOIN briefs b ON b.id = r.brief_id "
            "WHERE b.conversation_id=? AND r.delivered_ts IS NULL AND r.expires_ts > ? "
            "ORDER BY r.ts",
            (conversation_id, time.time()),
        ).fetchall()
        return [
            Result(
                id=r["id"],
                brief_id=r["brief_id"],
                ts=r["ts"],
                text=r["text"],
                delivered_ts=r["delivered_ts"],
                expires_ts=r["expires_ts"],
            )
            for r in rows
        ]

    async def mark_delivered(self, result_id: int) -> None:
        await self._run(self._mark_delivered_sync, result_id)

    def _mark_delivered_sync(self, result_id: int) -> None:
        c = self._db()
        c.execute("UPDATE results SET delivered_ts=? WHERE id=?", (time.time(), result_id))
        c.commit()

    async def results_for(self, brief_id: str) -> list[dict[str, Any]]:
        return await self._run(self._results_for_sync, brief_id)

    def _results_for_sync(self, brief_id: str) -> list[dict[str, Any]]:
        rows = self._db().execute(
            "SELECT ts, text, delivered_ts FROM results WHERE brief_id=? ORDER BY id",
            (brief_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def mark_exported(self, brief_id: str) -> None:
        await self._run(self._mark_exported_sync, brief_id)

    def _mark_exported_sync(self, brief_id: str) -> None:
        c = self._db()
        c.execute(
            "UPDATE results SET exported_ts=? WHERE brief_id=? AND exported_ts IS NULL",
            (time.time(), brief_id),
        )
        c.commit()

    # ─── the floor ───────────────────────────────────────────────────────────
    async def take_floor(self, device: str, hold_s: float) -> None:
        """Claim the floor until now + hold_s. Presence only, and cheap.

        Called at the MOMENT OF INTENT — the client's `start` frame, sent at
        key-press, before the user has finished speaking. That head start is the
        whole point: it gives the bench a second or two to abandon its generation
        before the presence actually needs the GPU.
        """
        await self._run(self._take_floor_sync, device, hold_s)

    def _take_floor_sync(self, device: str, hold_s: float) -> None:
        c = self._db()
        c.execute(
            "INSERT INTO floor (id, taken_until, device) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET taken_until=excluded.taken_until,"
            " device=excluded.device",
            (time.time() + hold_s, device),
        )
        c.commit()

    async def floor_taken(self) -> bool:
        return await self._run(self._floor_taken_sync)

    def _floor_taken_sync(self) -> bool:
        row = self._db().execute("SELECT taken_until FROM floor WHERE id=1").fetchone()
        return bool(row and row["taken_until"] > time.time())

    async def brief_status(self, brief_id: str) -> tuple[int | None, bool]:
        """(revision, floor_taken) in ONE read. The bench's inner-loop question.

        Both halves are asked together because they are asked together: every few
        dozen tokens the bench needs to know "is this still the brief I was
        given, and may I still have the GPU". Two round trips for that would put
        twice the disk in a hot path for no reason.
        """
        return await self._run(self._brief_status_sync, brief_id)

    def _brief_status_sync(self, brief_id: str) -> tuple[int | None, bool]:
        c = self._db()
        row = c.execute("SELECT revision FROM briefs WHERE id=?", (brief_id,)).fetchone()
        floor = c.execute("SELECT taken_until FROM floor WHERE id=1").fetchone()
        return (
            (row["revision"] if row else None),
            bool(floor and floor["taken_until"] > time.time()),
        )

    # ─── egress (the locality audit trail) ───────────────────────────────────
    async def record_egress(self, brief_id: str | None, endpoint: str, query: str) -> None:
        """One row per outbound query. Bench-only.

        `query` is the SEARCH TERMS, never the utterance. The presence's own
        words are not a search query and are never forwarded as one; the assert
        that they were not lives in roles/voice/tasks/verify.yml, and this is the
        table it reads.
        """
        await self._run(self._record_egress_sync, brief_id, endpoint, query)

    def _record_egress_sync(self, brief_id: str | None, endpoint: str, query: str) -> None:
        c = self._db()
        c.execute(
            "INSERT INTO egress (brief_id, ts, endpoint, query) VALUES (?,?,?,?)",
            (brief_id, time.time(), endpoint, query),
        )
        c.commit()

    async def egress_for(self, brief_id: str) -> list[dict[str, Any]]:
        return await self._run(self._egress_for_sync, brief_id)

    def _egress_for_sync(self, brief_id: str) -> list[dict[str, Any]]:
        rows = self._db().execute(
            "SELECT ts, endpoint, query FROM egress WHERE brief_id=? ORDER BY id",
            (brief_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── plumbing ────────────────────────────────────────────────────────────
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ledger is not open")
        return self._conn

    async def _run(self, fn, *args):  # type: ignore[no-untyped-def]
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)


def _brief(row: sqlite3.Row) -> Brief:
    return Brief(
        id=row["id"],
        conversation_id=row["conversation_id"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
        state=row["state"],
        statement=row["statement"],
        speaker=row["speaker"],
        device=row["device"],
        capability=row["capability"],
        revision=row["revision"],
    )
