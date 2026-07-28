# quality-gate — REQUIRED free-text polish via `qloop`

## Role

`qloop gate` is the lab quality authority for free-text when there is **no tool
or test oracle**. The orchestrator runs it; subagents do not. It is **not** an
agent replacement and **must never** run on code diffs.

## Decision tree (orchestrator — follow in order, no freelancing)

1. User/task tagged `GATE: skip` or `no-gate` → **do not call**; deliver draft.
2. Deliverable is code (diff, implement, tests, build, fix CI) → **do not call**;
   use coder/tester/reviewer + `@@RESULT`.
3. Deliverable is pure plumbing (one shell command, restart service, path copy)
   or a single short fact (≤2 sentences, one number/name) → **do not call**.
4. Else if deliverable is multi-constraint free-text for a human reader →
   **MUST call** `qloop gate` before final user-facing answer:
   - risk / rollback / migration narrative
   - proposal / client-facing wording
   - ops runbook / careful Q&A with multiple constraints
   - structured extraction or schema-shaped prose
   - planner/architect/doc-writer prose the user will act on
5. If unsure whether it is free-text quality work → **MUST call** (prefer
   over-gate on prose; never gate code).

## How to run (orchestrator or devops shell)

Resolve binary (first hit wins):

```bash
QLOOP=$(command -v qloop || true)
[ -z "$QLOOP" ] && [ -x .venv/bin/qloop ] && QLOOP=.venv/bin/qloop
[ -z "$QLOOP" ] && [ -x packages/quality-loop ] && QLOOP="$(pwd)/.venv/bin/qloop"
# if still empty: run `make qloop-venv` once, then retry; do not fake a polished answer
```

**Once per free-text deliverable** — do not re-run gate on the polished output
(see `loop-budget.md`).

Default invoke:

```bash
"$QLOOP" gate --strategy refine --worker general --judge general --rounds 2 --json \
  --timeout 180 \
  --task "TASK: <user goal and constraints>" \
  --baseline "$DRAFT"
```

| Kind of free-text | Extra flags |
|-------------------|-------------|
| Coding-related plan/risk narrative | `--worker coder` (judge stays `general`) |
| Client prose / proposals | optional `--playbook` writing playbook if path exists |
| Lab/ops free-text on ser5 | optional `--playbook /data/agentlab/playbooks/infra.md` |
| Explicit “give N alternatives” | `--strategy best_of_n -n 3` (never n>3) |

**Forbidden flags:** never `--worker heavy|judge|scout` or `--judge heavy|judge|scout`.

## Ask-alignment first (why this isn't "always refine")

`qloop gate --strategy refine` does **not** loop to make an answer stylistically
better. Each round's critique classifies the draft against the **verbatim
original ask** into exactly one of:

- `answered` — stop immediately; deliver as-is
- `partial` — missing something specific; one targeted revise round
- `off_target` — doesn't address what was asked; one targeted revise round
- `unsafe_or_invalid` — stop immediately; never revised, never delivered

If the draft already answers the ask, the gate returns on round 1 — it never
spends a second round "polishing" an already-correct answer. If `off_target`
or `unsafe_or_invalid` persists (or the critique stalls) past the round
budget, the gate **fails** rather than silently keeping a wrong-question
draft. See `packages/quality-loop/src/quality_loop/loops.py` for the protocol.

### Scope check (separate from the verdict above)

A draft can fully cover the ask and still be dangerous: it can act on an
assumption the ask left open, or do/add something nobody requested. Every
critique also carries `SCOPE: in_scope|exceeded`, and `exceeded` blocks
delivery **regardless of the verdict** — the same fail-fast treatment as
`unsafe_or_invalid`, with `reason=scope_exceeded`. This is deliberately not
auto-revised: an unconfirmed action is a "go ask the user" problem, not a
"try again" problem. The judge's own explanation (the specific assumption or
addition it named) is in `extra.critique` on the FAIL result — read it before
escalating, it's usually the whole story.

**Honest limit:** this is a text review of the *finished draft*, right before
delivery — a same-tier LLM judge reading prose. It has no visibility into
tool calls or side effects already taken while producing that draft. If an
agent already ran a command or wrote a file before drafting its final
answer, the gate cannot see or undo that — it can only stop the *narration*
of an unconfirmed action from being delivered as if it were fine. It is a
last-mile catch on wording, not a structural control on what agents do
upstream of it; don't treat a PASS here as proof nothing risky happened.

## Decision contract (honor exactly)

Stdout = one JSON object. Use it:

| decision | exit | reason (refine strategy) | action |
|----------|------|---------------------------|--------|
| ACCEPT | 0 | `answered` | deliver `answer` as the polished free-text |
| KEEP_BASELINE | 0 | `max_rounds` (partial, budget exhausted) | deliver `answer` (the baseline) |
| SKIP | 2 | `task_too_large` / `mini_down` | deliver original `$DRAFT`; do not retry gate |
| FAIL | 1 | `scope_exceeded` | do not deliver `answer`; tell the user what was assumed/added (`extra.critique`) and ask, don't act |
| FAIL | 1 | `unsafe_or_invalid` | do not deliver `answer`; surface `reason`, escalate |
| FAIL | 1 | `off_target` / `off_target_persistent` | do not deliver `answer`; surface `reason`, escalate (`_persistent` means it recurred across rounds, not just a single-round miss) |
| FAIL | 1 | `timeout` | one retry only |
| FAIL | 1 | other | surface `reason` and keep draft |

After a successful gate, final `@@RESULT` to the user should note `quality-gate: <decision>`.

## Subagent handoff convention

Prose subagents (planner, architect, doc-writer) that return user-facing text set:

```
handoff: run quality-gate (qloop gate) on this draft before delivering to user
```

Orchestrator treats that handoff as a hard next step when the draft is multi-constraint free-text.
