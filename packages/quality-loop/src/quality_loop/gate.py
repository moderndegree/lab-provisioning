"""Interactive quality gate: deterministic go/no-go around free-text answers.

Policy (see lab plan): warm models only, short timeouts, no playbook reflect.
Ask-alignment first: the refine strategy's critique step classifies the draft
against the *original* ask (answered/partial/off_target/unsafe_or_invalid,
see quality_loop.loops) and only iterates on a mismatch — it never loops just
to make an already-answered draft "better." A second, orthogonal check
(SCOPE: in_scope|exceeded) blocks delivery the same way unsafe_or_invalid
does whenever the draft acted on an assumption the ask left open, or did/
added something the ask never requested — that is a confirm-with-the-user
problem, not a quality problem, so it is never silently revised away.
Decisions: ACCEPT | KEEP_BASELINE | SKIP | FAIL with process exit codes.
  ACCEPT        verdict=answered and scope=in_scope
  KEEP_BASELINE verdict=partial (or unparsed) after the round budget
  FAIL          scope=exceeded (fail fast, any verdict), or
                verdict=unsafe_or_invalid (fail fast), or off_target
                (reason=off_target if only judged once — e.g. rounds=1 — or
                off_target_persistent if it recurred across rounds)
  SKIP          preflight/size guard tripped before any model call
FAIL results carry the judge's own explanation in `extra.critique` — the
specific assumption, addition, or gap it named — so the caller can see *why*
without re-deriving it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quality_loop.client import ChatClient, ChatError
from quality_loop.loops import (
    STRATEGIES,
    VERDICT_OFF_TARGET,
    VERDICT_UNSAFE,
    Trace,
)
from quality_loop.models import evicts_warm_pair
from quality_loop.playbook import Playbook

from quality_loop.loops import MAX_REFINE_ROUNDS as LOOPS_MAX_REFINE

ALLOWED_MODELS = frozenset({"general", "coder"})
MAX_ROUNDS_INTERACTIVE = 2  # hard cap for gate (≤ LOOPS_MAX_REFINE)
DEFAULT_TIMEOUT_S = 180.0
TASK_SOFT_CAP_CHARS = 12_000
PREFLIGHT_TIMEOUT_S = 3.0
assert MAX_ROUNDS_INTERACTIVE <= LOOPS_MAX_REFINE


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


    rounds = max(1, min(int(rounds), MAX_ROUNDS_INTERACTIVE))

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

            # Resolve (decision, accepted, answer, reason, exit_code, extra)
            # first, then build the GateResult once — every branch below
            # otherwise repeats the same eight fields.
            #
            # Scope check wins regardless of verdict: an answer that covers
            # the ask but acted on an unconfirmed assumption or added
            # something unrequested must never be auto-delivered.
            if trace.scope_exceeded:
                decision, accepted, answer, reason, exit_code = (
                    "FAIL", False, base, "scope_exceeded", 1,
                )
                extra = {"critique": _last_critique_text(trace)}
            elif trace.accepted:
                decision, accepted, answer, reason, exit_code = (
                    "ACCEPT", True, trace.answer, "answered", 0,
                )
                extra = {}
            elif trace.classification == VERDICT_UNSAFE:
                # Fail fast — never deliver a draft the judge flagged unsafe
                # or invalid, and never spend more rounds trying to fix it.
                decision, accepted, answer, reason, exit_code = (
                    "FAIL", False, base, "unsafe_or_invalid", 1,
                )
                extra = {"critique": _last_critique_text(trace)}
            elif trace.classification == VERDICT_OFF_TARGET:
                # "persistent" only if off_target was actually judged more
                # than once (trace.rounds > 1) — a single-round budget that
                # never got a second look isn't persistence, just a miss.
                reason = "off_target_persistent" if trace.rounds > 1 else "off_target"
                decision, accepted, answer, exit_code = "FAIL", False, base, 1
                extra = {"critique": _last_critique_text(trace)}
            else:
                # partial (or an unparsed verdict, which defaults to
                # partial): best effort was made toward the ask but ran out
                # of budget — deliver the pre-loop baseline rather than a
                # half-revised, never-re-checked draft.
                decision, accepted, answer, reason, exit_code = (
                    "KEEP_BASELINE", False, base, "max_rounds", 0,
                )
                extra = {}

            return GateResult(
                decision=decision,
                strategy="refine",
                accepted=accepted,
                answer=answer,
                baseline=base,
                rounds=trace.rounds,
                tokens=trace.total_completion_tokens,
                worker=worker,
                judge=judge,
                playbook=pb_name,
                reason=reason,
                exit_code=exit_code,
                extra=extra,
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


def _last_critique_text(trace: Trace) -> str:
    """The judge's own explanation for the last verdict — the specific
    assumption, addition, or gap it named — so a FAIL surfaces *why*, not
    just a bare reason code."""
    for step in reversed(trace.steps):
        if step.kind == "critique":
            return step.content[:500]
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
    """Critique/revise starting from a provided draft instead of generating first.

    Mirrors loops.refine's ask-alignment protocol via the shared
    loops._refine_round helper: classify the draft against the original ask,
    stop immediately on answered/unsafe_or_invalid/scope_exceeded, and only
    spend a revise round on partial/off_target.
    """
    from quality_loop.loops import (
        MAX_REFINE_ROUNDS,
        VERDICT_ANSWERED,
        Step,
        Trace,
        _refine_round,
    )

    rounds = max(1, min(int(max_rounds), MAX_REFINE_ROUNDS, MAX_ROUNDS_INTERACTIVE))
    trace = Trace(strategy="refine", task=task, answer=baseline)
    # synthetic generate step so tokens/rounds accounting stays readable
    trace.steps.append(
        Step(kind="generate", model=worker, content=baseline, meta={"seeded_baseline": True})
    )
    answer = baseline
    prev_critique = ""
    # A baseline draft is already supplied — there's no generate call to seed
    # with a system prompt, so it's unused on this path (unlike loops.refine).
    _ = system

    for round_no in range(1, rounds + 1):
        trace.rounds = round_no
        outcome = _refine_round(
            client, task, answer, worker=worker, judge=judge, prev_critique=prev_critique
        )
        trace.classification = outcome.verdict
        trace.scope_exceeded = outcome.scope_exceeded
        trace.steps.append(outcome.critique_step)
        if outcome.revise_step is not None:
            trace.steps.append(outcome.revise_step)
        if outcome.verdict == VERDICT_ANSWERED and not outcome.scope_exceeded:
            trace.accepted = True
        answer = outcome.answer
        if outcome.stop:
            break
        prev_critique = outcome.critique_content

    trace.answer = answer
    return trace
