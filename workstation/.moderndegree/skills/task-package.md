# task-package — understanding + context packaging (orchestrator)

## Role

Bad answers usually come from thin handoffs. **You (orchestrator) own problem
understanding and context packaging.** This skill is **mandatory** before
dispatching any subagent on non-trivial work.

**Packaging means POINTERS, not payloads.** Every subagent except `research` can
`read`, `grep`, `glob` and `list`. Their context is disposable — 262144 tokens
discarded when they finish — while yours has to survive the whole session. So a
file you paste costs you permanently and saves them nothing. Give coordinates
(paths, symbols, ranges), the goal, and done-when; let them fetch the detail.

Paste literal content only where it is NOT retrievable from the repo: the user's
own words, a runtime error, a log excerpt, a vault note, a decision you made. A
change under review is named by its PATHS, not pasted as a diff.

**A package that does not parse is not a package.** `task` arguments are JSON.
Measured 2026-08-22: a ~28k-character coder prompt full of fenced JSON and
function bodies failed with `Invalid input for tool task: JSON parsing failed:
Unterminated string`. Coder never ran. The ASK.md file was already on disk;
pointing at it would have been a complete handoff.

- No fenced code, no raw `{...}`, no function bodies in the `task` prompt.
- If ASK.md (or another spec file) is in the repo, name the path. Do not paste it.
- Architect/planner output stays in *your* context; hand coder **seams and
  paths**, not the plan verbatim.
- If a `task` call comes back `invalid`, retry once with a shorter pointer-only
  prompt. Do not resend the fat one. Do not write "ready to dispatch" as text.

## Decision tree (follow in order)

1. **Trivial plumbing only** (one known command, pure path copy, single fact
   already in the user message) → no full package; act or answer directly.
2. Else → build a **TASK PACKAGE** before planner/architect/coder/doc-writer/
   reviewer/security/tester/devops for the real work.
3. **Cortex (second brain) — dispatch `librarian` (RECALL), do not search
   yourself.** You hold no vault tools. A prose "cortex pass" never fired once
   in measured runs. If librarian returns `cortex: empty` or `unavailable`,
   continue and note it — do not block forever.
4. **Prefer tools over user questions.** If the repo/logs/vault can answer it,
   load it. Ask the user only for **blocking** unknowns (intent, priority, risk,
   client consent, which environment). Label everything else as an assumption.
5. **No thin handoffs — but thin is about INTENT, not volume.** A package is thin
   when the agent would have to invent requirements, guess the goal, or make a
   decision that is not theirs. It is NOT thin merely because file contents were
   not pasted; pointing at the right file is a complete handoff. Adding bulk to a
   package that lacks a clear done-when does not fix it.
6. On subagent `BLOCKED` / `FAIL` → enrich the package (exact gaps from handoff),
   re-gather if needed, re-dispatch. Do not retry the same thin prompt.

## Writing DONE-WHEN (this is where packages actually fail)

The package's acceptance list is the only thing a subagent is graded against.
Two rules, both learned the hard way:

**1. Everything that matters goes in the list.** A requirement mentioned in prose
but absent from done-when gets dropped — not maliciously, just deprioritised
against the items that are explicitly graded. If you would be unhappy to receive
the work without it, it is a done-when item, not a remark.

**2. Every criterion must be able to FAIL.** Before writing one, ask: *what would
a completely broken implementation print, and does my criterion reject it?* If a
broken system satisfies the check, the check is decoration.

| Decoration | Criterion |
|---|---|
| "returns valid JSON" | "returns a result parsed from live data, with the data fields non-null" |
| "a wrapper script exists" | "the wrapper runs from outside the project dir; paste its output" |
| "has unit tests" | "tests cover <named cases>; paste the summary line" |
| "handles errors gracefully" | "with the service stopped it exits non-zero and prints <x>" |
| "is documented" | "README states the chosen policy AND why; quote the section" |

Existence-shaped criteria ("X exists", "X is implemented") are satisfied by
creating a file. Execution-shaped criteria ("X was run, here is the output") are
not. Prefer the latter every time.

**3. Anything crossing a boundary needs one live check.** Network, another
process, the filesystem, a service. Offline tests prove logic and will pass
happily while the wiring is wrong — that is exactly how a tool ships having never
once reached the endpoint it exists to query. One real invocation, output pasted.

**4. Prefer criteria that can be executed before the code exists.** If a
done-when item can be turned into a command now — one that fails now, for the
right reason, and will pass when the work is done — write it into the package as
that command. A criterion whose first observed state was red is proven capable of
failing; one that has only ever been green may be asserting nothing.

**5. Name the dispatches you require.** A subagent role mentioned as advice
("design it first") does not get invoked; one named as a gate ("`architect` MUST
produce the interface before code is written") does. If a role's contribution is
necessary, make it a gate with its own @@RESULT.

**6. Third-party API shape is not something anyone here knows — it is something
that gets checked.** Training data goes stale, and the failure is quiet: plausible
syntax for a version that no longer exists, or an approach the maintainers stopped
recommending two releases ago. Nobody reports BLOCKED for this, because the model
does not know it is wrong.

So when the work touches a framework or library:

- **Name the skill to load.** `skill` lists what is installed (Next.js, React,
  shadcn, Payload, web-vitals/a11y/perf/SEO among others; run `skill` to see the
  current set). A skill named in the package gets loaded; one merely available does
  not — the same asymmetry as rule 5.
- **Add a criterion that executes against the real API**, not one that reads like
  it did. A typecheck, an import, one live call with output pasted. This is rule 3
  applied to a boundary that does not look like one: a library is someone else's
  system, and offline agreement with your memory of it proves nothing.

| Decoration | Criterion |
|---|---|
| "uses the current Next.js caching API" | "`pnpm typecheck` passes; paste the summary" |
| "follows shadcn conventions" | "loaded the `shadcn` skill; quote the rule applied and the line it changed" |
| "the SDK call is correct" | "one real call runs and returns non-null; paste the invocation and output" |

If no skill covers the technology, say so in the package rather than implying
coverage — an unstated gap is how a plan ends up asserting syntax nobody checked.

## Cortex pass (mandatory — a `librarian` dispatch, not a tool you hold)

You hold **no vault tools**. Measured 2026-08-05: a prose "cortex pass" never
fired once, which is why recall is a `task` call now.

Dispatch `librarian` with job RECALL before finishing the package. It searches
and returns 1–3 note excerpts + ids, or `cortex: empty` / `cortex: unavailable`.
Paste what it returns under **Lessons from vault**. Do not call `cortex_vault_*`
yourself — the tools are denied, and a denied call is not a BLOCKED report.

Capture is not this step. `qa` records a miss before it closes; a clean run
needs none. See `second-brain.md`.

If librarian reports the vault missing or empty, skip cortex content and proceed
(empty vault is fine). Note `cortex: empty` or `cortex: unavailable` in your
final summary — do not block forever.

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

Then **load** the repo side of the context map with grep/glob (pointers, not
payloads). Cortex excerpts come from the librarian dispatch, not from you.

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
| Paths, symbol names, done-when list | Entire chat history or whole repo |
| "Read ASK.md; implement the seams named below" | Fenced code / raw JSON / function bodies in `task` prompt |
| 1–3 vault note ids | Dumping cortex_vault_search full JSON |
| Interfaces + call sites that matter | Pasting architect/planner output verbatim |
| Prior failing @@RESULT text | "See earlier discussion" / "READY TO DISPATCH" |

Heuristic: a cold subagent must succeed **without** asking what the user meant.
Second heuristic: if the `task` prompt would look like source code, it will
fail JSON parse. Cut it.

## Who receives what

| Subagent | Package must include |
|----------|----------------------|
| planner / architect | Goal, constraints, done-when, paths + vault note ids |
| coder / tester / devops | Goal, done-when, seam/path list; they read ASK.md and the tree. Do not paste the plan. |
| reviewer / security | Changed paths + what changed + stated requirements; they read the files |
| doc-writer | Paths of the material to write from; they read it |

## Subagent contract (they already enforce; you must enable it)

Read-only agents return `BLOCKED` and name exact missing *intent or pointers*
rather than guessing. Missing file contents are not a gap — they can read.
When you see BLOCKED, fix the package and re-dispatch.

## User questions (template)

Only when blocking:

```text
I need these before I can proceed safely:
1. …
2. …
Assumptions I'll use if you don't answer: …
```

Do not ask the user for facts the tree or vault already contains.
