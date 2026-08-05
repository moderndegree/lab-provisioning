# moderndegree delivery contract

Every subagent ends its turn with exactly one block:

@@RESULT
status: PASS | FAIL | BLOCKED
summary: <one line>
evidence: <what you OBSERVED that proves the status — the command you ran and
           its actual output, a file path and line, a test summary line. Required
           for PASS. "Looks correct", "should work", and "implemented as
           specified" are not evidence.>
handoff: <what the orchestrator should do next>
@@END

The orchestrator does not proceed past a gate until it receives PASS **with
evidence**. A PASS whose evidence restates the intent rather than reporting an
observation is treated as FAIL — send it back.

`planner`, `architect`, `reviewer`, `security-auditor`, `doc-writer` and `deep`
hold read-only tools (`read`, `grep`, `glob`, `list`) and return analysis as text.
Only `coder`, `tester`, `devops`, `qa` and the orchestrator write to the
filesystem or run shell commands (`qa` runs things but never modifies them).

## Two orchestrator settings that are load-bearing

Measured 2026-08-05 by driving `opencode run` headlessly and counting `task`
(dispatch) calls in opencode's own database. Both were found by measurement after
prompt wording failed twice:

- **`build` must NOT have `reasoningEffort: "none"`.** With it, dispatches went to
  ZERO across repeated runs — the orchestrator built everything itself, 50 tests
  and all. Deciding "this warrants a subagent" is a judgement call, and that
  setting removes the budget to make it. Removing it restored dispatching.
- **`build` denies `edit`, `write`, `patch` and `bash`.** Reasoning alone was
  necessary but NOT sufficient: with the same config and prompt, one run used
  `task` twice and no bash, the next used `bash` 13 times and `task` once. The
  rule was advisory, so compliance was stochastic. Denying the tools made it
  structural — the run after that was `task` x4, zero bash, and reached `qa` for
  the first time, with zero blocked-tool attempts.

The general form: **prose advises, structure binds.** Where a rule matters, encode
it in config rather than in the prompt.

## tester and qa are different jobs

They are the two halves of "does it work", and either alone is a blind spot.

| | `tester` | `qa` |
|---|---|---|
| works from | the diff | the DONE-WHEN list |
| view | white box | black box |
| inputs | injected / mocked | the real dependency |
| proves | the LOGIC is right | the THING WORKS |
| runs | in the parallel critic batch | alone, after it, as the final gate |

Both can legitimately PASS while the system has never once functioned — a green
unit suite around dead wiring is the normal shape of that failure, not an exotic
one. `qa` exists to close it, and it is the last gate before work is handed back.

## Evidence over assertion

Work is judged by what was observed, not by what was intended. This applies to
every agent, every turn.

- **A claim about behaviour needs an observation.** "The endpoint returns JSON"
  is a claim; the pasted response is evidence. If you did not run it, say so.
- **Building is not verifying.** A file that exists, compiles, or type-checks has
  not been shown to work. Execute the thing — the CLI, the script, the unit,
  the request — and report what came back.
- **Offline tests prove LOGIC; a live call proves WIRING.** They fail differently
  and neither substitutes for the other. Anything that crosses a boundary — a
  network call, another process, the filesystem, a service — needs at least one
  real end-to-end invocation, however small. A suite that passes while the
  integration is broken is not a safety net; it is a blindfold.
- **Prefer checks that can FAIL.** "Returns valid JSON" passes on a response that
  reports total failure. "Returns a non-null result parsed from live data" cannot.
  When you state a done-when, ask what a broken system would print, and make sure
  the check rejects it.
- **Report the gap.** If something could not be verified, that goes in `evidence`
  as an explicit "not verified: <what, why>", not omitted. Silence reads as
  success and is the most expensive habit here.

## Loop budgets (anti-spin — all runs)

Full table: `.moderndegree/skills/loop-budget.md`. Hard stops:

| Limit | Cap |
|-------|-----|
| Re-dispatch same subagent | 2 |
| Package enrich → re-dispatch cycles | 3 |
| Cortex searches per task | 2 |
| Identical failing tool command | 2 |

On budget exhaust: escalate to the user with what you tried — do **not** keep
re-prompting “to think harder.” Orchestrator/devops keep `reasoningEffort: none`.

## TASK PACKAGE — context is the orchestrator's main job

Bad answers usually come from thin handoffs. Full rules:
`.moderndegree/skills/task-package.md`.

- Orchestrator **must** clarify the problem and build a **TASK PACKAGE** (goal,
  done-when, constraints, assumptions, pasted context excerpts) before dispatching
  any non-trivial subagent.
- **Cortex MCP** (when available): **must** `vault_search` → read top **1–3** notes
  into the package before dispatch. Prefer postmortems/playbooks. Cap volume —
  never dump the vault. If MCP is down, continue with repo tools and note it.
- Prefer **tools over user questions**; ask the user only for **blocking** unknowns.
- No-tools subagents work **only** from the package. Incomplete package → they
  return `BLOCKED` with exact gaps; orchestrator enriches and re-dispatches.
- Do not dump the whole repo or chat; paste relevant excerpts (prefill is expensive).
  repo or vault context.

## Placement rule (the architectural backbone)

Two `llama-server` endpoints on mini serve every agent. mini is
memory-bandwidth-bound: decode speed tracks **active parameters read per token**,
not total parameters. That is why both are MoE, and why **no agent may introduce
a dense model or point at a third endpoint**:

The split is by CONCURRENCY, not by difficulty. It is sized to measurements in
`mini/AGENTS.md`: the quality endpoint peaks at 2 concurrent streams and gets
worse at 4; the throughput endpoint peaks at 4 and gets worse at 8; and an
orchestrator supervising a fan-out costs only +3-4%.

- **`quality` → `http://mini:8090/v1`** — `qwen3.6-35b-a3b-mtp` (MoE 35B-A3B,
  3B active), 4 slots, MTP on. 88 t/s solo, and MTP is worth **+21% at one
  stream but -18% at two**, so this endpoint is for work on the critical path
  that runs largely alone:
  `build` (orchestrator), `planner`, `architect`, `coder`, `devops`, `deep`.

  `coder` lives here deliberately. It is the one agent whose output quality
  compounds, and it is usually the only thing running when it runs.

- **`throughput` → `http://mini:8091/v1`** — `gpt-oss-20b` (MoE 20B), 8 slots,
  no MTP. 33 t/s per stream at 4-way for 114 t/s aggregate. This endpoint is for
  the FAN-OUT — agents dispatched simultaneously against the same finished diff:
  `reviewer`, `security-auditor`, `tester`, `doc-writer`. Plus `qa`, which runs
  on this endpoint but SEQUENTIALLY, after the batch — see below.

  There are exactly four in the batch because four is where `:8091` peaks. Adding a
  fifth parallel critic buys nothing: at 8-way the endpoint delivers LESS
  aggregate throughput (81 t/s) than at 4-way and halves per-stream speed.

Steady state is therefore ~2 streams on `:8090` (orchestrator + one worker) and
up to 4 on `:8091` — which is exactly where both endpoints measure fastest.

Context per slot differs: **262144 on quality** (2 slots — the model's full native
window) and 131072 on throughput (8 slots). Both are partitioned statically at
startup, so a single session cannot exceed its endpoint's figure. There is no residency or eviction to reason about any
more: llama-server holds its weights for the process lifetime. (The previous
"two warm slots" wording described Ollama, which is now stopped.)

## Context budgets — why the orchestrator delegates reading

Agents on quality get **262144 tokens**; agents on throughput get **131072**.
That is per SLOT, fixed at server start. The orchestrator holds its window for the WHOLE session; every
subagent's is discarded when it finishes. Nine agents therefore give you roughly
nine independent working sets, but only if the orchestrator stops being the sole
reader.

Earlier experiments failed exactly here: subagents had no tools, so the
orchestrator had to read everything and paste it into every package, exhausted its
window mid-task, and compaction discarded the reasoning behind the plan.

So `planner`, `architect`, `reviewer`, `security-auditor`, `doc-writer` and `deep`
now hold `read`/`grep`/`glob`/`list`. Packages carry POINTERS — paths, symbols,
ranges — and the agent fetches its own detail. Literal content is pasted only when
it is not retrievable from the repo (the user's words, an error, a log, a vault
note, or the diff under review).

The orchestrator must not edit files, write implementations, run tests, or read a
file in full to "understand the repo". Those are dispatches, not shortcuts.

Roles live in agent prompts, not baked model variants. Reasoning mode is set
per agent via `reasoningEffort` in `opencode.json` (`"none"` on the
orchestrator and devops for deterministic tool dispatch; everything else
thinks). `reasoningEffort: "none"` maps to `reasoning_effort` on the wire and
**llama-server honours it** — verified 2026-08-04, 660 reasoning characters
drop to 0 with the same final answer. (`chat_template_kwargs.enable_thinking:
false` does the same thing and is what you use when calling the endpoint
directly rather than through opencode.) The `/no_think` soft switch and a
`think:false` body field do NOT
work through Ollama's `/v1` endpoint — never rely on them.

## Client-deliverable guardrails

- Default every client workspace to **Tier L (Sovereign)** — Ollama on `mini`.
- **Tier G (Governed)** — GitHub Copilot Pro+ with data retention disabled —
  is for the owner's own work, or client work with written consent.
- **Tier X (Personal)** — xAI SuperGrok / Grok Build — is for own repos,
  research, and long autonomous runs; never client-confidential.
- **Tier Z (Throwaway)** — OpenCode Zen free models — is OSS scaffolding only;
  never a client deliverable.
- Client-confidential work never goes through the Hermes Telegram or Discord
  gateways; the sovereign remote path is the tailnet-only Hermes dashboard or SSH.
- The orchestrator refuses to start implementation on a client repo without an
  approved OpenSpec change ID.

## Second brain / cortex (memory)

Vault at `/data/brain`; OpenCode loads **cortex** MCP (`opencode.json`). Full
rules: `.moderndegree/skills/second-brain.md` and the cortex pass in task-package.

- **Before work:** search vault → 1–3 notes into TASK PACKAGE.
- **After painful misses:** `vault_capture` (preferred) or postmortem file; promote
  durable rules to ACE playbooks.
- Do not treat unreviewed drafts as ground truth; no client secrets in the vault.

