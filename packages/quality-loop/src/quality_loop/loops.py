"""Loop strategies: test-time compute in exchange for quality.

Every strategy returns a Trace — the final answer plus every intermediate
step — so evals can score it, storage can persist it, and star.py can harvest
it for bootstrapped training data.

Strategies:
  single    — one shot (the baseline every loop must beat)
  refine    — generate → critique → revise, until the judge accepts
  best_of_n — sample k candidates at temperature, judge picks the winner
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from quality_loop.client import ChatClient

CRITIQUE_SYSTEM = """You are a strict reviewer. Assess the answer to the task.
Reply with exactly one line starting with VERDICT: ACCEPT or VERDICT: REVISE,
then (if REVISE) a numbered list of specific, actionable problems.
Accept only answers that are correct, complete, and directly address the task."""

REVISE_SYSTEM = """You are revising your previous answer using reviewer feedback.
Produce the full improved answer, not a diff. Fix every numbered problem."""

SELECT_SYSTEM = """You are judging candidate answers to the same task.
Reply with exactly one line: WINNER: <number> — then one sentence why.
Judge on correctness first, then completeness, then clarity."""


@dataclass
class Step:
    kind: str          # generate | critique | revise | select
    model: str
    content: str
    meta: dict = field(default_factory=dict)


@dataclass
class Trace:
    strategy: str
    task: str
    answer: str
    steps: list[Step] = field(default_factory=list)
    rounds: int = 0
    accepted: bool = False   # refine: judge accepted; best_of_n: judge picked

    @property
    def total_completion_tokens(self) -> int:
        return sum(s.meta.get("completion_tokens", 0) for s in self.steps)


def _step_from(kind: str, result, extra: dict | None = None) -> Step:
    meta = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_s": round(result.latency_s, 2),
    }
    if extra:
        meta.update(extra)
    return Step(kind=kind, model=result.model, content=result.content, meta=meta)


def single(client: ChatClient, task: str, worker: str = "general", system: str | None = None) -> Trace:
    """The one-shot baseline. Every loop strategy is judged against this."""
    result = client.ask(task, model=worker, system=system)
    trace = Trace(strategy="single", task=task, answer=result.content, rounds=1, accepted=True)
    trace.steps.append(_step_from("generate", result))
    return trace


def refine(
    client: ChatClient,
    task: str,
    worker: str = "general",
    judge: str = "general",
    max_rounds: int = 3,
    system: str | None = None,
) -> Trace:
    """Iterative self-refinement: generate, critique, revise until ACCEPT."""
    trace = Trace(strategy="refine", task=task, answer="")
    result = client.ask(task, model=worker, system=system)
    trace.steps.append(_step_from("generate", result))
    answer = result.content

    for round_no in range(1, max_rounds + 1):
        trace.rounds = round_no
        critique = client.ask(
            f"TASK:\n{task}\n\nANSWER:\n{answer}",
            model=judge,
            system=CRITIQUE_SYSTEM,
        )
        verdict_accept = bool(re.search(r"VERDICT:\s*ACCEPT", critique.content))
        trace.steps.append(_step_from("critique", critique, {"accept": verdict_accept}))
        if verdict_accept:
            trace.accepted = True
            break
        revision = client.chat(
            [
                {"role": "system", "content": REVISE_SYSTEM},
                {"role": "user", "content": task},
                {"role": "assistant", "content": answer},
                {"role": "user", "content": f"Reviewer feedback:\n{critique.content}\n\nProduce the improved answer."},
            ],
            model=worker,
        )
        trace.steps.append(_step_from("revise", revision))
        answer = revision.content

    trace.answer = answer
    return trace


def best_of_n(
    client: ChatClient,
    task: str,
    n: int = 4,
    worker: str = "general",
    judge: str = "general",
    temperature: float = 0.9,
    system: str | None = None,
) -> Trace:
    """Sample n candidates, then have the judge pick the best one."""
    trace = Trace(strategy="best_of_n", task=task, answer="", rounds=1)
    candidates: list[str] = []
    for i in range(n):
        result = client.ask(task, model=worker, system=system, temperature=temperature, seed=i)
        trace.steps.append(_step_from("generate", result, {"candidate": i + 1}))
        candidates.append(result.content)

    numbered = "\n\n".join(f"CANDIDATE {i + 1}:\n{c}" for i, c in enumerate(candidates))
    selection = client.ask(f"TASK:\n{task}\n\n{numbered}", model=judge, system=SELECT_SYSTEM)
    match = re.search(r"WINNER:\s*(\d+)", selection.content)
    winner = int(match.group(1)) if match else 1
    winner = min(max(winner, 1), n)
    trace.steps.append(_step_from("select", selection, {"winner": winner, "parsed": bool(match)}))
    trace.answer = candidates[winner - 1]
    trace.accepted = bool(match)
    return trace


STRATEGIES = {
    "single": single,
    "refine": refine,
    "best_of_n": best_of_n,
}
