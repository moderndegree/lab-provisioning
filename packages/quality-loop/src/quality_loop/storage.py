"""Run tracking: SQLite for queryable results, JSONL for full traces.

Layout under the data dir (QUALITY_LOOP_DATA / LOOPKIT_DATA fallback;
default ~/.quality-loop; the agentlab role sets it to /data/agentlab on ser5):

  runs.db                    one row per (run, task) result
  traces/<run_id>.jsonl      full step-by-step traces, one task per line
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sqlite3
import uuid
from pathlib import Path

from quality_loop.loops import Trace

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    run_id      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    suite       TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    worker      TEXT NOT NULL,
    score       REAL NOT NULL,
    accepted    INTEGER NOT NULL,
    rounds      INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    answer      TEXT NOT NULL,
    judge_model TEXT,
    critique_rounds INTEGER,
    verdict_parsed INTEGER,
    scope_parsed INTEGER,
    PRIMARY KEY (run_id, task_id)
);
"""

# Columns added after the initial schema — applied via ALTER TABLE to any
# runs.db that predates them, since CREATE TABLE IF NOT EXISTS is a no-op on
# an existing table.
_ADDED_COLUMNS = {
    "judge_model": "judge_model TEXT",
    "critique_rounds": "critique_rounds INTEGER",
    "verdict_parsed": "verdict_parsed INTEGER",
    "scope_parsed": "scope_parsed INTEGER",
}


def data_dir() -> Path:
    """QUALITY_LOOP_DATA preferred; LOOPKIT_DATA accepted; else ~/.quality-loop."""
    raw = (
        os.environ.get("QUALITY_LOOP_DATA")
        or os.environ.get("LOOPKIT_DATA")
        or str(Path.home() / ".quality-loop")
    )
    root = Path(raw)
    (root / "traces").mkdir(parents=True, exist_ok=True)
    return root


class RunStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        (self.root / "traces").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "runs.db")
        self.db.execute(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(results)")}
        for name, ddl in _ADDED_COLUMNS.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE results ADD COLUMN {ddl}")

    def new_run_id(self) -> str:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{stamp}-{uuid.uuid4().hex[:6]}"

    def record(
        self,
        run_id: str,
        suite: str,
        task_id: str,
        strategy: str,
        worker: str,
        score: float,
        trace: Trace,
    ) -> None:
        # Judge reliability (not just answer quality): every critique step
        # already carries parsed/scope_parsed in its meta (see loops._refine_round);
        # this is where it stops being trace-only and becomes queryable.
        critique_steps = [s for s in trace.steps if s.kind == "critique"]
        judge_model = critique_steps[-1].model if critique_steps else None
        verdict_parsed = (
            int(all(s.meta.get("parsed", False) for s in critique_steps))
            if critique_steps else None
        )
        scope_parsed = (
            int(all(s.meta.get("scope_parsed", False) for s in critique_steps))
            if critique_steps else None
        )
        self.db.execute(
            """INSERT OR REPLACE INTO results (
                   run_id, started_at, suite, task_id, strategy, worker, score,
                   accepted, rounds, completion_tokens, answer,
                   judge_model, critique_rounds, verdict_parsed, scope_parsed
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                suite,
                task_id,
                strategy,
                worker,
                score,
                int(trace.accepted),
                trace.rounds,
                trace.total_completion_tokens,
                trace.answer,
                judge_model,
                len(critique_steps),
                verdict_parsed,
                scope_parsed,
            ),
        )
        self.db.commit()
        with open(self.root / "traces" / f"{run_id}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"task_id": task_id, "score": score, **dataclasses.asdict(trace)}) + "\n")

    def summary(self, run_id: str | None = None) -> list[dict]:
        """Per-run aggregates, newest first (or just the one run).

        started_at is second-resolution, so two runs of the same combo recorded
        within the same second tie. That tie is not cosmetic: matrix() takes the
        first row per combo, so an arbitrary tiebreak can report a stale run as
        the latest and push a bake-off toward the wrong model. rowid is the
        table's implicit insertion counter (the PRIMARY KEY is composite TEXT,
        so rowid is untouched), which gives a true, monotonic tiebreak with no
        schema migration.
        """
        where, args = "", ()
        if run_id:
            where, args = "WHERE run_id = ?", (run_id,)
        rows = self.db.execute(
            f"""SELECT run_id, suite, strategy, worker,
                       COUNT(*) AS tasks,
                       ROUND(AVG(score), 3) AS mean_score,
                       SUM(completion_tokens) AS tokens,
                       MIN(started_at) AS started_at,
                       MAX(rowid) AS seq
                FROM results {where}
                GROUP BY run_id, suite, strategy, worker
                ORDER BY started_at DESC, seq DESC""",
            args,
        ).fetchall()
        cols = ["run_id", "suite", "strategy", "worker", "tasks", "mean_score", "tokens", "started_at", "seq"]
        out = [dict(zip(cols, r)) for r in rows]
        for row in out:  # ordering-only column; keep the public row shape stable
            row.pop("seq", None)
        return out

    def matrix(self) -> list[dict]:
        """One row per (suite, strategy, worker) — the most recent run of each
        combo. Feeds the Phase 1 baseline matrix / quality summary."""
        latest: dict[tuple[str, str, str], dict] = {}
        for row in self.summary():  # already newest-first
            key = (row["suite"], row["strategy"], row["worker"])
            if key not in latest:
                latest[key] = row
        return sorted(latest.values(), key=lambda r: (r["suite"], r["strategy"], r["worker"]))

    def judge_stats(self) -> list[dict]:
        """VERDICT/SCOPE parse rate per judge model, by day. This is the
        reliability signal a judge-model bake-off needs alongside answer
        quality: a candidate can score well on suites while quietly drifting
        off the VERDICT:/SCOPE: format, which this would otherwise hide."""
        rows = self.db.execute(
            """SELECT judge_model, substr(started_at, 1, 10) AS day,
                      COUNT(*) AS critiqued_tasks,
                      ROUND(AVG(verdict_parsed), 3) AS verdict_parse_rate,
                      ROUND(AVG(scope_parsed), 3) AS scope_parse_rate
               FROM results
               WHERE judge_model IS NOT NULL
               GROUP BY judge_model, day
               ORDER BY day DESC, judge_model"""
        ).fetchall()
        cols = ["judge_model", "day", "critiqued_tasks", "verdict_parse_rate", "scope_parse_rate"]
        return [dict(zip(cols, r)) for r in rows]
