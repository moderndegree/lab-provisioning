"""Run tracking: SQLite for queryable results, JSONL for full traces.

Layout under the data dir (LOOPKIT_DATA, default ~/.loopkit; the agentlab
role sets it to /data/agentlab on ser5):

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

from loopkit.loops import Trace

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
    PRIMARY KEY (run_id, task_id)
);
"""


def data_dir() -> Path:
    root = Path(os.environ.get("LOOPKIT_DATA", Path.home() / ".loopkit"))
    (root / "traces").mkdir(parents=True, exist_ok=True)
    return root


class RunStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        (self.root / "traces").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "runs.db")
        self.db.execute(SCHEMA)
        self.db.commit()

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
        self.db.execute(
            "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
            ),
        )
        self.db.commit()
        with open(self.root / "traces" / f"{run_id}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"task_id": task_id, "score": score, **dataclasses.asdict(trace)}) + "\n")

    def summary(self, run_id: str | None = None) -> list[dict]:
        """Per-run aggregates, newest first (or just the one run)."""
        where, args = "", ()
        if run_id:
            where, args = "WHERE run_id = ?", (run_id,)
        rows = self.db.execute(
            f"""SELECT run_id, suite, strategy, worker,
                       COUNT(*) AS tasks,
                       ROUND(AVG(score), 3) AS mean_score,
                       SUM(completion_tokens) AS tokens,
                       MIN(started_at) AS started_at
                FROM results {where}
                GROUP BY run_id, suite, strategy, worker
                ORDER BY started_at DESC""",
            args,
        ).fetchall()
        cols = ["run_id", "suite", "strategy", "worker", "tasks", "mean_score", "tokens", "started_at"]
        return [dict(zip(cols, r)) for r in rows]
