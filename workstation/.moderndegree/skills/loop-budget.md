# loop-budget — anti-spin circuit breakers (all runs)

Models and agents must not thrash. Hard budgets for every non-trivial task.

## Hard budgets (orchestrator)

| Budget | Max | On exceed |
|--------|-----|-----------|
| Re-dispatch **same** subagent after FAIL/BLOCKED | **2** | Escalate to user; do not call it a third time with the same package |
| TASK PACKAGE enrich → re-dispatch cycles | **3** | Stop, return best effort + gaps, ask user |
| Librarian RECALL dispatches per task | **2** | Use what you have; no search thrash |
| Tool retries on identical failing command | **2** | Change approach or BLOCKED |
| `task` call returned `invalid` (JSON parse) | **1** shorter retry | Pointers only, no code fences. Do not resend the fat prompt. Then escalate. |
| Subagents in parallel for same subtask | **1** | No duplicate planner/coder races |
| Critics dispatched in parallel on one diff | **4** | `reviewer`, `security-auditor`, `tester`, `doc-writer` — this matches the quality endpoint's four slots; a 5th slows all of them |
| Orchestrator files read in full | **0** | Locate with grep/glob, delegate the reading. Your context is the only one that is not disposable |

## Reasoning / thinking

- devops: `reasoningEffort: "none"` (already in config) — stay terse.
- Orchestrator must **not** have `reasoningEffort: "none"`. Measured 2026-08-05,
  it drops subagent dispatches to zero. Thinking stays on so it can decide to
  delegate.
- Do **not** re-prompt a model solely to “think harder” without new context.
- Prefer one short plan over multi-hop self-debate in the primary agent.

## Detect spin → stop

Stop and escalate to the user when any of these hold:

1. Same BLOCKED reason twice without new package fields filled  
2. Same shell/tool error twice with the same command  
3. Agent output is only meta-reasoning with no @@RESULT after a full turn  
4. Re-dispatch would not change the TASK PACKAGE  
5. You wrote "READY TO DISPATCH" / a plan summary and did not call `task` this turn — that is a failed turn, not a stop. Call `task`.  
6. Second `invalid` `task` call — stop and tell the user the package would not parse.  

Escalation template:

```text
Stopped: loop budget exhausted.
What I tried: …
Stuck on: …
Need from you: … (or confirm I should stop)
```

## Cortex

- Dispatch `librarian` (RECALL). You hold no vault tools.
- Librarian: at most **2** search calls, then **1–3** `cortex_vault_get_note`.
  Do not re-search with the same query.  

## Related

- `task-package.md` — package once, enrich with budget  
- `second-brain.md` — capture after failure, not in a loop  
