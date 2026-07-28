# task-package — understanding + context packaging (orchestrator)

## Role

Bad answers usually come from thin handoffs. **You (orchestrator) own problem
understanding and context packaging.** Subagents — especially no-tools ones —
only see what you paste. This skill is **mandatory** before dispatching any
subagent on non-trivial work.

## Decision tree (follow in order)

1. **Trivial plumbing only** (one known command, pure path copy, single fact
   already in the user message) → no full package; act or answer directly.
2. Else → build a **TASK PACKAGE** before planner/architect/coder/doc-writer/
   reviewer/security/tester/devops for the real work.
3. **Cortex (second brain) — required when MCP tools are available.** Before
   finishing the package, query the vault for prior lessons (see below). If cortex
   MCP is missing/errors, continue with repo tools only and note
   `cortex: unavailable` in your final summary — do not block forever.
4. **Prefer tools over user questions.** If the repo/logs/vault can answer it,
   load it. Ask the user only for **blocking** unknowns (intent, priority, risk,
   client consent, which environment). Label everything else as an assumption.
5. **No thin handoffs.** If a no-tools subagent would need to invent files, APIs,
   or requirements, gather more context first — do not dispatch yet.
6. On subagent `BLOCKED` / `FAIL` → enrich the package (exact gaps from handoff),
   re-gather if needed, re-dispatch. Do not retry the same thin prompt.
7. After a solid free-text draft, follow `quality-gate.md` (qloop). Quality-gate
   does **not** replace packaging, cortex, or tool-gathering.

## Cortex pass (mandatory when tools exist)

Use the **cortex** MCP server (configured in `opencode.json`). Pull **1–3**
relevant notes max into the package — never the whole vault.

| Step | Tool | How |
|------|------|-----|
| 1. Search lessons | `vault_search` | Query from goal keywords + domain (e.g. "ser5 ollama eviction", "postmortem handoff") |
| 2. Optional list | `vault_list_notes` | Filter `kind` if useful (`playbook`, `note`) |
| 3. Read hits | `vault_get_note` | Only the top 1–3 ids that match this task |
| 4. Optional neighborhood | `vault_backlinks` / `vault_local_graph` | When a hit is central and you need related context |
| 5. Skip capture here | — | Capture/postmortems are `second-brain.md` after work |

Paste into TASK PACKAGE under **Context** as short excerpts + note ids
(e.g. `notes/postmortems/2026-…`, `playbooks/infra`). Prefer postmortems and
playbooks over random MOCs unless the MOC is the topic.

If `vault_stats` shows `exists: false` or zero notes, skip cortex content and
proceed (empty vault is fine).

## Understanding pass (before first subagent)

Produce (internally; keep terse):

| Field | Purpose |
|-------|---------|
| Goal | One-sentence outcome |
| Why | User/business outcome (5-whys until outcome, not feature) |
| Done-when | 3–5 observable checks |
| Constraints / non-goals | Bounds; what not to do |
| Blocking unknowns | Only these may become user questions |
| Assumptions | Labeled guesses if work proceeds |
| Context map | Paths, diffs, logs, APIs, **+ cortex note ids** to load |

Then **load** the context map with tools (repo + cortex). Paste **excerpts**.

## TASK PACKAGE skeleton (paste into every subagent prompt)

```markdown
## TASK PACKAGE
### Goal
### Why / user outcome
### Scope / non-goals
### Done-when (checks)
### Constraints (tier, safety, style)
### Context
<!-- repo excerpts + paths; cortex note excerpts + ids -->
### Lessons from vault (if any)
<!-- 1–3 bullets from postmortems/playbooks -->
### Assumptions (labeled)
### Out of scope for you
### Return format
@@RESULT with status / summary / handoff
```

## Context size (mini prefill is expensive)

| Do | Don't |
|----|--------|
| Relevant files, diffs, error logs | Entire chat history or whole repo |
| 1–3 vault notes / playbook bullets | Dumping vault_search full JSON |
| Interfaces + call sites that matter | Every transitive dependency |
| Prior failing @@RESULT text | "See earlier discussion" |

Heuristic: a cold subagent must succeed **without** asking what the user meant.

## Who receives what

| Subagent | Package must include |
|----------|----------------------|
| planner / architect | Goal, constraints, done-when, relevant design/code + vault lessons |
| coder / tester / devops | Full package; they may read more files but must not redefine goal |
| reviewer / security | Diff + stated requirements + surrounding excerpts needed to judge |
| doc-writer | All material to write from; no "look it up" |

## Subagent contract (they already enforce; you must enable it)

No-tools agents return `BLOCKED` and name exact missing inputs rather than
guessing. When you see that, fix the package and re-dispatch.

## User questions (template)

Only when blocking:

```text
I need these before I can proceed safely:
1. …
2. …
Assumptions I'll use if you don't answer: …
```

Do not ask the user for facts the tree or vault already contains.
