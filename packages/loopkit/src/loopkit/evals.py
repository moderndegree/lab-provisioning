"""Eval runner: measure whether a loop strategy actually beats the baseline.

Task suites are JSONL files, one task per line:

  {"id": "gsm-1", "prompt": "...", "expected": "42", "match": "numeric"}

match modes:
  exact    — normalized string equality
  contains — expected appears in the answer (case-insensitive)
  regex    — expected is a regex searched in the answer
  numeric  — last number in the answer equals expected (tolerance 1e-6)

The final answer is extracted from an ANSWER: line when present, so prompts
should ask for one — reasoning models otherwise bury the result mid-prose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loopkit.client import ChatClient
from loopkit.loops import STRATEGIES, Trace
from loopkit.playbook import Playbook
from loopkit.storage import RunStore

ANSWER_SUFFIX = 'End your reply with a line "ANSWER: <final answer>".'


@dataclass
class Task:
    id: str
    prompt: str
    expected: str
    match: str = "contains"


def load_suite(path: str | Path) -> list[Task]:
    tasks = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        tasks.append(
            Task(
                id=str(obj.get("id", f"task-{line_no}")),
                prompt=obj["prompt"],
                expected=str(obj["expected"]),
                match=obj.get("match", "contains"),
            )
        )
    return tasks


def extract_answer(text: str) -> str:
    """Prefer the last 'ANSWER: ...' line; fall back to the whole text."""
    matches = re.findall(r"ANSWER:\s*(.+)", text)
    return matches[-1].strip() if matches else text.strip()


def _last_number(text: str) -> float | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(nums[-1]) if nums else None


def score(task: Task, answer_text: str) -> float:
    answer = extract_answer(answer_text)
    expected = task.expected.strip()
    if task.match == "exact":
        return float(answer.strip().lower() == expected.lower())
    if task.match == "contains":
        return float(expected.lower() in answer.lower())
    if task.match == "regex":
        return float(bool(re.search(expected, answer)))
    if task.match == "numeric":
        got, want = _last_number(answer), _last_number(expected)
        return float(got is not None and want is not None and abs(got - want) < 1e-6)
    raise ValueError(f"unknown match mode: {task.match}")


def run_suite(
    client: ChatClient,
    suite_path: str | Path,
    strategy: str = "single",
    worker: str = "general",
    playbook: Playbook | None = None,
    reflect: bool = False,
    store: RunStore | None = None,
    limit: int | None = None,
    on_result: Callable[[Task, Trace, float], None] | None = None,
    **strategy_kwargs,
) -> dict:
    """Run every task through the strategy; record, optionally reflect."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {sorted(STRATEGIES)}")
    tasks = load_suite(suite_path)
    if limit:
        tasks = tasks[:limit]
    store = store or RunStore()
    run_id = store.new_run_id()
    suite_name = Path(suite_path).stem
    loop_fn = STRATEGIES[strategy]

    scores: list[float] = []
    for task in tasks:
        system = playbook.as_context() or None if playbook else None
        prompt = f"{task.prompt}\n\n{ANSWER_SUFFIX}"
        trace = loop_fn(client, prompt, worker=worker, system=system, **strategy_kwargs)
        task_score = score(task, trace.answer)
        scores.append(task_score)
        store.record(run_id, suite_name, task.id, strategy, worker, task_score, trace)
        if on_result:
            on_result(task, trace, task_score)
        if playbook and reflect:
            steps = "; ".join(f"{s.kind}({s.model})" for s in trace.steps)
            outcome = f"score={task_score} ({'PASS' if task_score >= 1 else 'FAIL'})"
            playbook.reflect(
                client,
                task.prompt,
                f"strategy={strategy}; steps: {steps}; final answer: {extract_answer(trace.answer)[:400]}",
                outcome,
            )

    return {
        "run_id": run_id,
        "suite": suite_name,
        "strategy": strategy,
        "worker": worker,
        "tasks": len(tasks),
        "mean_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
    }
