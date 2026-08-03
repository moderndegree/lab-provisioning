"""Loop strategies: test-time compute in exchange for quality.

Every strategy returns a Trace — the final answer plus every intermediate
step — so evals can score it, storage can persist it, and star.py can harvest
it for bootstrapped training data.

Strategies:
  single    — one shot (the baseline every loop must beat)
  refine    — generate → critique → revise, until the judge accepts

Anti-spin (all loop calls):
  - reasoning_effort="none" so models do not burn the budget on hidden thinking
  - hard max_rounds / n caps
  - early stop when revise produces no change or critique repeats

refine's critique step is an alignment check, not a style pass: it judges
whether the draft answers the original ask, not whether it could be polished
further. See VERDICT_* below — the whole point is to stop as soon as the ask
is answered and to fail fast rather than iterate forever on a draft that
never lands on the ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from quality_loop.client import ChatClient

# Hard ceilings — callers may pass lower, never higher effectively for refine.
MAX_REFINE_ROUNDS = 3
# Keep critiques short so a spinny model cannot ramble forever.
CRITIQUE_MAX_TOKENS = 512
# Loop calls never enable model "thinking" — verified on Ollama /v1.
LOOP_REASONING = "none"

# Classification labels for the alignment critique.
VERDICT_ANSWERED = "answered"
VERDICT_PARTIAL = "partial"
VERDICT_OFF_TARGET = "off_target"
VERDICT_UNSAFE = "unsafe_or_invalid"

_VERDICT_RE = re.compile(
    r"VERDICT:\s*(answered|partial|off_target|unsafe_or_invalid|unsafe|invalid)", re.I
)
# A judge that drops the "_or_invalid" suffix still means unsafe_or_invalid —
# treat it as an alias rather than silently downgrading to "partial", which
# would defeat the fail-fast-on-unsafe guarantee for a near-miss label.
_VERDICT_ALIASES = {"unsafe": VERDICT_UNSAFE, "invalid": VERDICT_UNSAFE}

# Orthogonal to VERDICT: an answer can fully cover the ask and still overstep
# it (an unconfirmed assumption acted on, or an unrequested addition/action).
# That is a confirm-with-the-user problem, not a "make it better" problem, so
# it is judged and gated separately from ask-coverage. Matched as a whole
# token (not a substring) so e.g. "in_scope_but_actually_..." can't be
# mistaken for a clean "in_scope".
_SCOPE_RE = re.compile(r"SCOPE:\s*(\S+)", re.I)

CRITIQUE_SYSTEM = """Check whether ANSWER addresses the ORIGINAL ASK — an alignment
check, not a style pass. Do not suggest improvements the ask never requested.

Reply with exactly two lines, then (if needed) a numbered list of at most 5
specific, actionable items:

VERDICT: <answered|partial|off_target|unsafe_or_invalid>
  answered           ask fully addressed; nothing more is needed
  partial            part of the ask is addressed; something specific is missing
  off_target         does not address what was actually asked
  unsafe_or_invalid  unsafe, false, or broken; must not be delivered as-is

SCOPE: <in_scope|exceeded>
  exceeded means the answer treated an assumption as settled where the ask
  left it ambiguous, or did/added anything beyond what was asked — even if
  correct or helpful. A confident guess stated as fact, or an unrequested
  action taken without asking first, is "exceeded".

List (only if VERDICT is not answered, or SCOPE is exceeded): name exactly
what is missing/wrong versus the ask, or what was assumed/added beyond it.

Be concise; do not restate the answer. If fully addressed with nothing
assumed or added beyond it, say VERDICT: answered and SCOPE: in_scope —
do not chain new issues endlessly."""

REVISE_SYSTEM = """You are revising your previous answer using reviewer feedback that
identifies gaps between your answer and the original ask. Produce the full
improved answer, not a diff. Close every numbered gap. Do not add sections
the ask did not request. Stop when the ask is addressed."""

@dataclass
class Step:
    kind: str          # generate | critique | revise
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
    accepted: bool = False   # refine: verdict == answered
    classification: str = ""  # refine: last alignment verdict (VERDICT_*)
    scope_exceeded: bool = False  # refine: judge flagged an unconfirmed assumption/addition

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


def _loop_kw(**extra) -> dict:
    """Common kwargs: never enable model thinking inside strategies."""
    kw = {"reasoning_effort": LOOP_REASONING}
    kw.update(extra)
    return kw


def single(client: ChatClient, task: str, worker: str = "general", system: str | None = None) -> Trace:
    """The one-shot baseline. Every loop strategy is judged against this."""
    result = client.ask(task, model=worker, system=system, **_loop_kw())
    trace = Trace(strategy="single", task=task, answer=result.content, rounds=1, accepted=True)
    trace.steps.append(_step_from("generate", result))
    return trace


def _parse_verdict(content: str) -> tuple[str, bool]:
    """Return (label, parsed). Unparseable critiques default to 'partial' so a
    malformed judge reply cannot masquerade as 'answered' — it just costs one
    more (budget-capped) revision round instead. A near-miss label ("unsafe",
    "invalid") is resolved through _VERDICT_ALIASES rather than falling into
    that same default, so it still fails fast instead of being quietly
    treated as ordinary partial progress."""
    match = _VERDICT_RE.search(content)
    if not match:
        return VERDICT_PARTIAL, False
    label = match.group(1).lower()
    return _VERDICT_ALIASES.get(label, label), True


def _parse_scope_exceeded(content: str) -> tuple[bool, bool]:
    """Return (exceeded, parsed). Unlike verdict parsing — where an unparsed
    reply safely defaults to 'partial' and just costs another round — an
    unparsed or non-exact SCOPE line here fails CLOSED (exceeded=True). This
    check exists specifically to block unconfirmed assumptions/actions;
    silently letting a malformed or off-format judge reply through would
    defeat the entire point of the check."""
    match = _SCOPE_RE.search(content)
    if not match:
        return True, False
    token = match.group(1).strip(".,;:()*`'\"").lower()
    if token == "exceeded":
        return True, True
    if token == "in_scope":
        return False, True
    return True, False


@dataclass
class _RoundOutcome:
    """One critique (+ conditional revise) round of the ask-alignment loop."""
    critique_step: Step
    revise_step: Step | None
    critique_content: str
    verdict: str
    scope_exceeded: bool
    answer: str
    stop: bool  # loop should not continue (converged, failed fast, or stalled)


def _refine_round(
    client: ChatClient,
    task: str,
    answer: str,
    *,
    worker: str,
    judge: str,
    prev_critique: str,
) -> _RoundOutcome:
    """Run one critique round, and — unless it stops the loop — one revise
    round. Shared by loops.refine() and gate._refine_from_baseline() so the
    classification/stop rules (the actual point of this feature) live in
    exactly one place instead of two hand-synced copies."""
    critique = client.ask(
        f"ORIGINAL ASK:\n{task}\n\nDRAFT:\n{answer}",
        model=judge,
        system=CRITIQUE_SYSTEM,
        **_loop_kw(max_tokens=CRITIQUE_MAX_TOKENS),
    )
    verdict, verdict_parsed = _parse_verdict(critique.content)
    scope_exceeded, scope_parsed = _parse_scope_exceeded(critique.content)
    # Identical critique twice → stop (judge spinning / no new signal)
    stalled = bool(prev_critique) and _norm(critique.content) == _norm(prev_critique)
    critique_step = _step_from(
        "critique",
        critique,
        {
            "verdict": verdict,
            "parsed": verdict_parsed,
            "scope_exceeded": scope_exceeded,
            "scope_parsed": scope_parsed,
            "stalled": stalled,
        },
    )
    # Scope wins regardless of VERDICT: an unconfirmed assumption or
    # unrequested addition is a "check with the user" problem, not something
    # a revise round should quietly patch over. unsafe_or_invalid and a
    # stalled (repeating) critique fail fast the same way.
    if scope_exceeded or verdict in (VERDICT_UNSAFE,) or stalled or verdict == VERDICT_ANSWERED:
        return _RoundOutcome(
            critique_step, None, critique.content, verdict, scope_exceeded, answer, True
        )

    revision = client.chat(
        [
            {"role": "system", "content": REVISE_SYSTEM},
            {"role": "user", "content": task},
            {"role": "assistant", "content": answer},
            {
                "role": "user",
                "content": f"Reviewer feedback (gap to the ask):\n{critique.content}\n\nProduce the improved answer.",
            },
        ],
        model=worker,
        **_loop_kw(),
    )
    revise_step = _step_from("revise", revision)
    new_answer = revision.content
    # No textual change → further rounds will not help
    no_change = _norm(new_answer) == _norm(answer)
    return _RoundOutcome(
        critique_step, revise_step, critique.content, verdict, scope_exceeded,
        answer if no_change else new_answer, no_change,
    )


def refine(
    client: ChatClient,
    task: str,
    worker: str = "general",
    judge: str = "general",
    max_rounds: int = 3,
    system: str | None = None,
) -> Trace:
    """Generate, then check alignment to the original ask; revise only on a
    partial/off_target mismatch. Stops immediately on 'answered' or
    'unsafe_or_invalid' — ask-alignment first, not endless polish."""
    rounds = max(1, min(int(max_rounds), MAX_REFINE_ROUNDS))
    trace = Trace(strategy="refine", task=task, answer="")
    result = client.ask(task, model=worker, system=system, **_loop_kw())
    trace.steps.append(_step_from("generate", result))
    answer = result.content
    prev_critique = ""

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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


STRATEGIES = {
    "single": single,
    "refine": refine,
}
