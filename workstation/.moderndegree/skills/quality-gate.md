# quality-gate — free-text polish via `qloop` (not for code)

## Role

`qloop gate` spends local test-time compute on mini (Tier L) to improve
**prose / extraction / risk-writeup** drafts when there is **no tool or test
oracle**. It is **not** an agent replacement and **must not** run on code diffs.

## When to call

Call only when **all** of these hold:

1. The deliverable is free-text (plan narrative, risk list, proposal section,
   structured extraction wording) — **not** a code change.
2. The task has multiple constraints or quality matters for a human reader.
3. The task is not tagged `GATE: skip` / `no-gate`.
4. mini is the intended route (Tier L). Never send client-confidential material
   off Tier L.

## When **not** to call

- Coder/tester work, diffs, builds, test failures → use `@@RESULT` + tools.
- Reviewer / security-auditor already provided a structured critique.
- Short factual lookups or pure plumbing.

## Command

Prefer a local `qloop` on PATH (workstation: `make qloop-venv` then
`.venv/bin/qloop`, or a ser5 SSH wrapper). Example:

```bash
qloop gate --strategy refine --worker general --rounds 2 --json \
  --task "Improve this draft for completeness and rollback clarity: …" \
  --baseline "$DRAFT"
```

Optional playbook inject (no reflect in interactive gate):

```bash
qloop gate … --playbook /data/agentlab/playbooks/writing.md
```

## Decision contract

Stdout is one JSON object. Exit codes:

| decision | exit | action |
|----------|------|--------|
| ACCEPT / KEEP_BASELINE | 0 | use `answer` |
| SKIP | 2 | keep original draft; do not retry the gate |
| FAIL | 1 | surface `reason`; at most one retry if `timeout` |

Warm models only (`general`, `coder`). Never pass `heavy`, `judge`, or `scout`.
