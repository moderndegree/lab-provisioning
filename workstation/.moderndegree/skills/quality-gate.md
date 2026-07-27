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

## Decision contract (honor exactly)

Stdout = one JSON object. Use it:

| decision | exit | action |
|----------|------|--------|
| ACCEPT / KEEP_BASELINE | 0 | deliver `answer` as the polished free-text |
| SKIP | 2 | deliver original `$DRAFT`; do not retry gate |
| FAIL | 1 | if `reason=timeout`, one retry only; else surface `reason` and keep draft |

After a successful gate, final `@@RESULT` to the user should note `quality-gate: <decision>`.

## Subagent handoff convention

Prose subagents (planner, architect, doc-writer) that return user-facing text set:

```
handoff: run quality-gate (qloop gate) on this draft before delivering to user
```

Orchestrator treats that handoff as a hard next step when the draft is multi-constraint free-text.
