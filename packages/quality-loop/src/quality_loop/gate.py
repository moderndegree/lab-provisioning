"""Interactive quality gate: deterministic go/no-go around free-text answers.

Policy (see lab plan): warm models only, short timeouts, no playbook reflect.
Decisions: ACCEPT | KEEP_BASELINE | SKIP | FAIL with process exit codes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quality_loop.client import ChatClient, ChatError
from quality_loop.loops import STRATEGIES, Trace
from quality_loop.models import evicts_warm_pair
from quality_loop.playbook import Playbook

ALLOWED_MODELS = frozenset({"general", "coder"})
MAX_ROUNDS_INTERACTIVE = 2
MAX_BEST_OF_N = 3
DEFAULT_TIMEOUT_S = 180.0
TASK_SOFT_CAP_CHARS = 12_000
PREFLIGHT_TIMEOUT_S = 3.0


@dataclass
class GateResult:
    decision: str  # ACCEPT | KEEP_BASELINE | SKIP | FAIL
    strategy: str
    accepted: bool
    answer: str
    baseline: str
    rounds: int = 0
    tokens: int = 0
    worker: str = "general"
    judge: str = "general"
    playbook: str | None = None
    reason: str = ""
    exit_code: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "decision": self.decision,
            "strategy": self.strategy,
            "accepted": self.accepted,
            "answer": self.answer,
            "baseline": self.baseline,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "worker": self.worker,
            "judge": self.judge,
            "playbook": self.playbook,
            "reason": self.reason,
        }
        if self.extra:
            payload["extra"] = self.extra
        return json.dumps(payload)


def preflight_reachable(base_url: str, timeout_s: float = PREFLIGHT_TIMEOUT_S) -> bool:
    """Cheap reachability check against the OpenAI-compatible /models endpoint."""
    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def _reject_eviction(worker: str, judge: str) -> GateResult | None:
    for label, alias in (("worker", worker), ("judge", judge)):
        if alias not in ALLOWED_MODELS or evicts_warm_pair(alias):
            return GateResult(
                decision="FAIL",
                strategy="",
                accepted=False,
                answer="",
                baseline="",
                worker=worker,
                judge=judge,
                reason="eviction_risk",
                exit_code=1,
                extra={"bad": label, "alias": alias},
            )
    return None


def run_gate(
    client: ChatClient,
    task: str,
    *,
    strategy: str = "refine",
    worker: str = "general",
    judge: str = "general",
    baseline: str | None = None,
    rounds: int = MAX_ROUNDS_INTERACTIVE,
    n: int = MAX_BEST_OF_N,
    playbook_path: str | None = None,
    skip_preflight: bool = False,
) -> GateResult:
    """Run the interactive gate; never loads non-warm models."""
    if strategy not in STRATEGIES:
        return GateResult(
            decision="FAIL",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            reason="bad_strategy",
            exit_code=1,
        )

    if strategy == "best_of_n" and n > MAX_BEST_OF_N:
        return GateResult(
            decision="FAIL",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            reason="n_too_large",
            exit_code=1,
            extra={"n": n, "max": MAX_BEST_OF_N},
        )

    if rounds > MAX_ROUNDS_INTERACTIVE:
        rounds = MAX_ROUNDS_INTERACTIVE

    bad = _reject_eviction(worker, judge)
    if bad:
        bad.strategy = strategy
        bad.baseline = baseline or ""
        bad.answer = baseline or ""
        return bad

    if len(task) > TASK_SOFT_CAP_CHARS:
        return GateResult(
            decision="SKIP",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            reason="task_too_large",
            exit_code=2,
        )

    if not skip_preflight and not preflight_reachable(client.base_url):
        return GateResult(
            decision="SKIP",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            reason="mini_down",
            exit_code=2,
        )

    system: str | None = None
    pb_name: str | None = None
    if playbook_path:
        path = Path(playbook_path)
        if path.is_file():
            system = Playbook(path).as_context() or None
            pb_name = path.name
        # missing playbook: continue without (plan fallback)

    try:
        if strategy == "single":
            trace = STRATEGIES["single"](client, task, worker=worker, system=system)
            base = baseline if baseline is not None else trace.answer
            return GateResult(
                decision="ACCEPT",
                strategy="single",
                accepted=True,
                answer=trace.answer,
                baseline=base,
                rounds=trace.rounds,
                tokens=trace.total_completion_tokens,
                worker=worker,
                judge=judge,
                playbook=pb_name,
                reason="accepted",
                exit_code=0,
            )

        if strategy == "refine":
            # If caller supplied a draft baseline, seed by treating first generate
            # as that draft only when we still need a model pass for critique.
            # Simplest correct path: always run refine; baseline = first generate
            # unless --baseline provided (then first generate is still done by refine;
            # for provided baseline we critique/revise that text).
            if baseline:
                trace = _refine_from_baseline(
                    client, task, baseline, worker=worker, judge=judge,
                    max_rounds=rounds, system=system,
                )
                base = baseline
            else:
                trace = STRATEGIES["refine"](
                    client, task, worker=worker, judge=judge,
                    max_rounds=rounds, system=system,
                )
                base = _first_generate(trace) or trace.answer

            if trace.accepted:
                decision, reason = "ACCEPT", "accepted"
            else:
                decision, reason = "KEEP_BASELINE", "max_rounds"
                answer = base
                return GateResult(
                    decision=decision,
                    strategy="refine",
                    accepted=False,
                    answer=answer,
                    baseline=base,
                    rounds=trace.rounds,
                    tokens=trace.total_completion_tokens,
                    worker=worker,
                    judge=judge,
                    playbook=pb_name,
                    reason=reason,
                    exit_code=0,
                )
            return GateResult(
                decision=decision,
                strategy="refine",
                accepted=True,
                answer=trace.answer,
                baseline=base,
                rounds=trace.rounds,
                tokens=trace.total_completion_tokens,
                worker=worker,
                judge=judge,
                playbook=pb_name,
                reason=reason,
                exit_code=0,
            )

        # best_of_n
        trace = STRATEGIES["best_of_n"](
            client, task, n=n, worker=worker, judge=judge, system=system,
        )
        base = baseline if baseline is not None else (_first_generate(trace) or trace.answer)
        if trace.accepted:
            return GateResult(
                decision="ACCEPT",
                strategy="best_of_n",
                accepted=True,
                answer=trace.answer,
                baseline=base,
                rounds=trace.rounds,
                tokens=trace.total_completion_tokens,
                worker=worker,
                judge=judge,
                playbook=pb_name,
                reason="accepted",
                exit_code=0,
            )
        # judge parse failed — prefer baseline when provided
        if baseline is not None:
            return GateResult(
                decision="KEEP_BASELINE",
                strategy="best_of_n",
                accepted=False,
                answer=baseline,
                baseline=baseline,
                rounds=trace.rounds,
                tokens=trace.total_completion_tokens,
                worker=worker,
                judge=judge,
                playbook=pb_name,
                reason="judge_unparsed",
                exit_code=0,
            )
        return GateResult(
            decision="ACCEPT",
            strategy="best_of_n",
            accepted=False,
            answer=trace.answer,  # candidate 1 fallback inside loops.best_of_n
            baseline=base,
            rounds=trace.rounds,
            tokens=trace.total_completion_tokens,
            worker=worker,
            judge=judge,
            playbook=pb_name,
            reason="judge_unparsed",
            exit_code=0,
        )
    except ChatError as err:
        return GateResult(
            decision="FAIL",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            playbook=pb_name,
            reason="timeout" if "timeout" in str(err).lower() else "chat_error",
            exit_code=1,
            extra={"error": str(err)[:200]},
        )
    except Exception as err:  # noqa: BLE001 — gate must always return a decision
        return GateResult(
            decision="FAIL",
            strategy=strategy,
            accepted=False,
            answer=baseline or "",
            baseline=baseline or "",
            worker=worker,
            judge=judge,
            playbook=pb_name,
            reason="error",
            exit_code=1,
            extra={"error": str(err)[:200]},
        )


def _first_generate(trace: Trace) -> str:
    for step in trace.steps:
        if step.kind == "generate":
            return step.content
    return ""


def _refine_from_baseline(
    client: ChatClient,
    task: str,
    baseline: str,
    *,
    worker: str,
    judge: str,
    max_rounds: int,
    system: str | None,
) -> Trace:
    """Critique/revise starting from a provided draft instead of generating first."""
    import re

    from quality_loop.loops import CRITIQUE_SYSTEM, REVISE_SYSTEM, Step, Trace, _step_from

    trace = Trace(strategy="refine", task=task, answer=baseline)
    # synthetic generate step so tokens/rounds accounting stays readable
    trace.steps.append(
        Step(kind="generate", model=worker, content=baseline, meta={"seeded_baseline": True})
    )
    answer = baseline

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
                {
                    "role": "user",
                    "content": f"Reviewer feedback:\n{critique.content}\n\nProduce the improved answer.",
                },
            ],
            model=worker,
        )
        trace.steps.append(_step_from("revise", revision))
        answer = revision.content

    trace.answer = answer
    return trace
