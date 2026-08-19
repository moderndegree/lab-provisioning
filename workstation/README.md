# workstation — opencode runtime config

This directory holds the **workstation (dev box)** half of the moderndegree local
agent stack. It is *not* provisioned by Ansible — `mini` (inference) and `ser5`
(Hermes gateway) are. These files are the opencode configuration you copy onto, or
symlink into, your development machine.

## Layout

| Path | Goes where on the workstation | Purpose |
|---|---|---|
| [opencode.json](opencode.json) | project root **or** `~/.config/opencode/` | Two llama-server providers + model limits + the 11-agent team (plus `deep` and `research` escalations) |
| [AGENTS.md](AGENTS.md) | project root | Injected at session start; the `@@RESULT` contract |
| [.moderndegree/prompts/](.moderndegree/prompts/) | project root | Per-agent system prompts referenced by `opencode.json` |
| [docs/business-layer.md](docs/business-layer.md) | reference | Tier L/G/X/Z routing, sovereignty, OpenSpec gates |
| [.moderndegree/skills/task-package.md](.moderndegree/skills/task-package.md) | project root | **Required** problem understanding + context packaging before subagent handoffs |
| [.moderndegree/skills/second-brain.md](.moderndegree/skills/second-brain.md) | project root | Postmortems + playbook promotion after misses (`/data/brain`) |
| [.moderndegree/skills/loop-budget.md](.moderndegree/skills/loop-budget.md) | project root | Anti-spin circuit breakers for agents |
| [.moderndegree/skills/tdd.md](.moderndegree/skills/tdd.md) | project root | Test-first discipline; tests sit at the public seam, not an inner helper |
| [bin/](bin/) | ser5 `~/.local/bin/` via `roles/devtools` | Measurement harness for the agent chain — see [bin/README.md](bin/README.md) |

## The split that drives everything

Two `llama-server` endpoints on mini. **As of 2026-08-14 the split is by
DIFFICULTY, not by concurrency** — that inverts the previous design, and the
reason is measurement, not taste. Roles live in the agent prompts rather than
baked model variants.

- **`quality` — `http://mini:8090/v1`** — `qwen3.6-35b-a3b-mtp` (MoE 35B-A3B,
  3B active), **4 slots**, MTP n-max 3, **262144 ctx per slot**, ~80–90 t/s
  solo. GENERAL: `build`, `planner`, `devops`, `qa`, and the critic fan-out
  (`reviewer`, `security-auditor`, `tester`, `doc-writer`, `librarian`).
- **`deep` — `http://mini:8091/v1`** — `qwen3.8-27b` (DENSE 27B hybrid),
  **1 slot**, **262144 ctx**, ~19 t/s. CODING + hard design: `architect`,
  `coder`, `deep`. They share the single slot, so a concurrent dispatch queues.

  **`architect` was promoted here 2026-08-14 on measured evidence**, not on the
  published benchmarks (which compare 3.8 to the DENSE Qwen3.6-27B, not to the
  35B-A3B we run). Head-to-head on one design brief: 3.8 specified
  `FOR UPDATE SKIP LOCKED` as its work-queue primitive; 3.6 specified a polling
  worker pool with no concurrency control — a double-delivery bug in an
  at-least-once system. 3.8 also quantified its trade-offs (throughput ceiling,
  migration path, when it would choose differently) where 3.6 argued generically.
  Cost: 59s -> 247s for that call. Paid ONCE per task, not per loop.

  `coder` later moved here too: implementation quality compounds, and 3.8 is
  the coding model. It is usually alone on `:8091` (`architect` is once per
  task; `deep` is rare). Critics stay on `:8090` so the fan-out still has four
  slots.

  Caveat: the architect comparison is ONE task under greedy decoding. It is
  evidence, not proof. Reverting is a one-line model change in `opencode.json`.

**Why the concurrency split was abandoned.** It existed to keep the critic
fan-out off the interactive endpoint. Measured 2026-08-14 at n=4, back-to-back:
`qwen3.6` with MTP did 106.4 aggregate / 90.8 solo, while the throughput model
(`nemotron-3.5-lightning`) did 92.1 / 70.8 at the same ctx/slot. The dedicated
throughput endpoint was **losing on both axes**, so it was retired rather than
resized, and the critics moved onto the driver's 4 slots.

The cost is real and accepted: a dedicated `gpt-oss-20b` endpoint still measures
140.8 aggregate against 109.4 for this design — **~22% slower fan-out** — in
exchange for critics that reason on 35B-A3B instead of a 20B, one fewer endpoint,
and ~40 GiB of headroom. `gpt-oss-20b` stays staged on mini, so restoring a third
endpoint is a config edit, not a download.

Consequence to know: a 4-wide fan-out now fully occupies `:8090` and the
orchestrator queues behind it. MTP's aggregate cost is structural — raising
`parallel` does not buy it back (np 2 → 8 moved n=4 by 3 tok/s).

Why this split: `:8090` peaks at 2 concurrent streams and is SLOWER at 4, while
MTP is worth +21% at one stream but -18% at two. `:8091` peaks at 4 and is
slower at 8. So there are exactly four fan-out agents, and steady state is ~2
streams on `:8090` plus up to 4 on `:8091` — where both endpoints measure
fastest. Numbers in `../mini/AGENTS.md`.

mini is memory-bandwidth-bound, not compute-bound. Decode speed tracks active
parameters per token, so MoE wins; a dense model is a mistake here.

Context on quality is **262,144 per slot** — the model's native maximum — from
`ctx: 524288` across `parallel: 2`. It is partitioned statically at startup
(`-c` total / `-np` slots) — a single session cannot exceed it however idle the box is, and
there is no per-request `num_ctx`. KV cost is ctx × slots, so raise one only by
lowering the other. Prefill degrades with depth — 1025 t/s at depth 0, 652 at
65536, 486 t/s measured on a real 137622-token request — so a packed 262144
prompt costs roughly 9-12 minutes before the first token: the window is capacity,
not a target. Prefix caching is what makes deep context practical, since a warm
prefix skips prefill entirely (~12x on TTFT).

(This section used to describe two *warm Ollama models* with residency and
eviction. Ollama is stopped as of 2026-08; llama-server holds its weights for
the process lifetime, so there is no eviction to reason about.)

## Writing an ask the team can execute

`build` completes an underspecified ask by REASONING, not by asking. It fills in
goal, done-when, constraints, scope, exclusions and assumptions itself, states
them back as a handshake (`Goal / Done when / Assuming / Not doing`), and starts
immediately without waiting for approval. A one-line ask is fine — read the
handshake and redirect if it guessed wrong.

It asks only when proceeding either way would waste the work or be unsafe
(destructive operations, client consent, environment, a genuine fork in intent) —
and then in one message, capped at three questions, each with a default you can
accept rather than compose.

When you want control instead, the thing that actually moves quality is the
acceptance list, not length. A run that produced a working-looking tool which had
never once reached its endpoint failed because every criterion was satisfiable by
broken software:

```text
GOAL         <one line — what exists at the end>
CONSTRAINTS  <language, deps, things it must not do>

DONE WHEN — each item needs pasted evidence from a real run
  1. <ran WHAT, observed WHAT>  ... not "X works"
  2. ...

PROCESS — the ONLY way to reach planner, architect, deep or librarian
  - <role> MUST <do X> before <Y>
```

That last block is not optional decoration, and it is the single highest-leverage
line you can add. Measured 2026-08-05: across 65 `coder` dispatches, **`architect`
and `deep` had never run once** and `planner` had run 3 times — while the
orchestrator prompt asked for all of them the entire time. Adding

```text
PROCESS
  - architect MUST design the module boundary and the error contract before any implementation starts.
  - planner MUST produce the implementation plan before coder is dispatched.
```

produced `librarian → architect → planner → coder → [4 critics] → qa` on the
first try, and reproduced. The reason is structural: the orchestrator decides by
reading the text nearest the decision, and before its first dispatch the nearest
text is your ask. Nothing in a system prompt competes with that.

So: if the hard part of the task is a design question, say so in PROCESS. If you
don't, it will go straight to `coder` — competently, and without ever having
designed anything.

Four rules, each learned by watching it go wrong:

- **Every criterion must reject a broken build.** "Returns valid JSON" passes on a
  response reporting total failure. "Returns non-null fields parsed from live
  data" cannot.
- **Say "was run", not "exists".** A wrapper script that exists satisfies
  "a wrapper exists" and still crashes on import.
- **`PROCESS` schedules a role; only `DONE WHEN` proves anything.** If it matters
  that something is true at the end, it is a criterion — putting it in `PROCESS`
  gets you an agent that thought about it, not a fact that was checked.

  Measured 2026-08-05. An ask said *"`architect` MUST enumerate every route and
  server action that can reach the runner, and design the check so a new route
  cannot silently bypass it."* `architect` ran, `security-auditor` ran, and the
  delivered fix guarded a new API route while leaving the pre-existing server
  action published and unauthenticated — a complete bypass. `qa` passed the work
  having tested only the guarded route. The requirement needed to be a criterion
  with a command attached, not a design instruction.

- **Check every criterion is achievable on the CURRENT tree before you ask for
  it.** An impossible criterion does not produce a BLOCKED report; it produces a
  creative reading of the criterion.

  Same run: the ask said "`pnpm lint` passes". Lint was already failing on
  `HEAD` with 10 pre-existing errors in untouched files. The team satisfied the
  criterion by switching the offending rules off project-wide. Ask for "no NEW
  lint errors versus HEAD, paste the before/after counts" when the baseline is
  dirty — and run the command yourself first if you do not know.

- **A criterion that asserts a NON-event must name its verification command.**
  "Wrote no file outside the project", "left no process running", "made no
  network call" — state the check, do not leave the instrument to the agent.
  Write `paste the output of: find /data/brain -newermt <start>`, not "confirm
  nothing outside the repo was written".

  Measured twice on the same task. Given the loose wording, `qa` passed the
  criterion once by grepping a source file and listing `/tmp`, and once by
  running `git status --short` — from inside the repo, an instrument that cannot
  by construction see outside it. On the first of those runs five files had in
  fact been written to the forbidden directory. Adding a section to `qa.md`
  telling it how to verify absences did **not** change the method on the next
  run; naming the command in the ask is the lever that works.
- **Anything not in DONE WHEN gets dropped.** Requirements mentioned in passing
  lose to the ones being graded. If you would be unhappy to receive the work
  without it, it is a criterion.

Naming a role as advice ("design it first") does not dispatch it — `architect`
and `deep` ran zero times on a task whose hardest part was a design question.
Make it a gate if you need it.

## Windows workstation (cockpit)

The primary dev box is a Windows gaming PC, not an inference node. Global
opencode config lives at `%USERPROFILE%\.config\opencode\opencode.json`;
per-project config may live at the project root. `AGENTS.md` and
`.moderndegree\prompts\` also go in the project root.

The gaming PC runs **no local models**. Its 32 GB DDR5 and 16 GB RTX 4080 Super
lose to mini's 128 GB unified pool on every axis; it is a cockpit that drives
mini. Use GitHub Copilot CLI for your own repos and Tier G work. Use opencode
for client-confidential Tier L work. The full decision tree lives in
[../docs/operating-manual.md](../docs/operating-manual.md).

## Non-Ollama providers (opt-in, not wired by default)

`opencode.json` declares `github-copilot` and `xai` as empty provider blocks.
That enables opencode's built-in catalog entries but does **not** authenticate
them — no agent uses them until you log in:

```powershell
opencode auth login    # pick github-copilot, then xai
```

Only two agents route off Ollama, and both are opt-in subagents you have to
invoke by name:

| Agent | Model | Tier | Guardrail |
|---|---|---|---|
| `heavy` | `ollama/gpt-oss:120b` | L | Still local, but loading it **evicts a warm model** on mini. Deliberate use only, never in a loop. |
| `research` | `xai/grok-build-0.1` | X | Third-party. **Never** give it client-confidential material. |

If you never log in, both `build` and every default subagent keep running
entirely on mini, so the sovereign path is the failure-safe default.

## Cortex MCP (second brain)

`opencode.json` enables a local **cortex** MCP server from the sibling
[ai-workstation](../../ai-workstation) repo, pointed at `CORTEX_VAULT_DIR=/data/brain`
(ser5 vault after `enable_brain`). Adjust `--dir` / vault path if your layout
differs. Requires `pnpm install` in ai-workstation.

Agents **must** use it when available:

1. **Before dispatch** — `vault_search` → up to 3 notes in the TASK PACKAGE  
   (see `.moderndegree/skills/task-package.md`).
2. **After painful misses** — `vault_capture` / postmortem  
   (see `.moderndegree/skills/second-brain.md`).

## Reasoning control (verified against Ollama 0.31.2)

Reasoning mode is per **agent**, set via `reasoningEffort` passthrough in
`opencode.json`: `"none"` on the orchestrator and devops (terse, deterministic
tool dispatch), unset (thinking on for the Qwen stack) everywhere else.

Two things that look like they work but **don't** through the `/v1` endpoint:
the `/think`/`/no_think` soft switches in prompts, and a `think: false` body
field. Both were tested and are ignored. `reasoning_effort: "none"` is the
only /v1 mechanism that disables thinking; native `/api/chat` honours
`think: false` for scripts.
