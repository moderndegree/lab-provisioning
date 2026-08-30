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

## What makes the critic fan-out fire

Measured 2026-08-05: baseline never reached 4/4 critics in six runs; the fix ran
5/5. The lever is **`coder`'s `handoff` line naming the fan-out** — the
orchestrator picks its next move by reading the `@@RESULT` in front of it, and
that beats any rule in its system prompt. Same lesson as `task-package.md` rule 5
("named as a gate gets invoked; mentioned as advice does not"), one level down.

Three things that looked like fixes and measured worse — `temperature: 0.2`,
denying `read` to `build`, and cutting INTAKE alone. Full evidence, plus the two
run-eating hangs (backgrounded servers, `/tmp` reads), are in
`docs/agent-chain-findings.md`. Read it before changing orchestrator settings.

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
re-prompting “to think harder.” `devops` keeps `reasoningEffort: none`; the
orchestrator must NOT — see the load-bearing settings above.

## TASK PACKAGE — context is the orchestrator's main job

Bad answers usually come from thin handoffs. Full rules:
`.moderndegree/skills/task-package.md`.

- Orchestrator **must** clarify the problem and build a **TASK PACKAGE** (goal,
  done-when, constraints, assumptions, **paths not payloads**) before dispatching
  any non-trivial subagent. `task` arguments are JSON: fenced code and a pasted
  implementation plan break the parser (`invalid` / unterminated string,
  measured 2026-08-22) and the subagent never starts. Point at ASK.md / seams.
  An `invalid` result → one shorter retry, then stop. Narrating "READY TO
  DISPATCH" without a `task` call is a failed turn.
- **Cortex (second brain): dispatch `librarian`, do not search yourself.** The
  orchestrator holds no vault tools — a prose "cortex pass" never fired once in
  measured runs, so recall is a `task` call now. `librarian` returns 1–3 note
  excerpts + ids for the package, and records lessons after a run with a real
  miss. If cortex is down it says so; continue and note it.
- Prefer **tools over user questions**; ask the user only for **blocking** unknowns.
- No-tools subagents work **only** from the package. Incomplete package → they
  return `BLOCKED` with exact gaps; orchestrator enriches and re-dispatches.
- Do not dump the whole repo or chat; paste relevant excerpts (prefill is expensive).
  repo or vault context.

## Placement rule (the architectural backbone)

Two `llama-server` endpoints on mini serve every agent. The split is by
**role**, not by concurrency:

- **`quality` → `http://mini:8090/v1`** — `qwen3.6-35b-a3b-mtp` (MoE 35B-A3B,
  3B active), 4 slots, MTP on, ~80–90 t/s solo. **General**: chat,
  orchestration, planning, critique, docs, infra.
  `build`, `planner`, `devops`, `reviewer`, `security-auditor`, `tester`,
  `doc-writer`, `qa`, `librarian`.

- **`deep` → `http://mini:8091/v1`** — `qwen3.8-27b` (dense 27B, hybrid
  attention), 1 slot, MTP on, ~19 t/s. **Coding + hard design**:
  `architect`, `coder`, `deep`. One slot — a second dispatch queues.

`coder` is on 27B because implementation quality compounds and 3.8 is the
coding model. It is usually the only thing on `:8091` when it runs
(`architect` is once per task; `deep` is rare). Do not put the critic
fan-out on `:8091` — those four stay on `:8090` so they actually run in
parallel.

mini is memory-bandwidth-bound: the 35B is fast because only 3B weights
move per token. The 27B reads the whole model per token; that slowness is
accepted on the coding path only. No agent may point at a third endpoint
or start Ollama (it is stopped). llama-server holds weights for the
process lifetime.

Context is **262144 per slot** on both endpoints, partitioned at startup.
A single session cannot exceed its endpoint's figure.

## Context budgets — why the orchestrator delegates reading

Every agent gets **262144 tokens** per slot. The orchestrator holds its
window for the WHOLE session; every subagent's is discarded when it
finishes. Nine agents therefore give you roughly nine independent working
sets, but only if the orchestrator stops being the sole reader.

Earlier experiments failed exactly here: subagents had no tools, so the
orchestrator had to read everything and paste it into every package, exhausted its
window mid-task, and compaction discarded the reasoning behind the plan.

So `planner`, `architect`, `reviewer`, `security-auditor`, `doc-writer` and `deep`
now hold `read`/`grep`/`glob`/`list`. Packages carry POINTERS — paths, symbols,
ranges — and the agent fetches its own detail. Literal content is pasted only when
it is not retrievable from the repo (the user's words, an error, a log, a vault
note). The critics get changed PATHS, not a pasted diff — the orchestrator has no
`bash` and cannot produce one.

The orchestrator must not edit files, write implementations, run tests, or read a
file in full to "understand the repo". Those are dispatches, not shortcuts.

Roles live in agent prompts, not baked model variants. Reasoning mode is set
per agent via `reasoningEffort` in `opencode.json` (`"none"` on devops only.
The orchestrator must NOT have it — measured 2026-08-05, it drops subagent
dispatches to zero. Everything else thinks). `reasoningEffort: "none"` maps to `reasoning_effort` on the wire and
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

- **Before work:** dispatch `librarian` (RECALL) → 1–3 notes into TASK PACKAGE.
- **After painful misses:** `qa` calls `cortex_vault_capture` before it closes
  (the run ends at its `@@RESULT`; an orchestrator capture afterwards never
  fires). Promote durable rules to ACE playbooks.
- Do not treat unreviewed drafts as ground truth; no client secrets in the vault.

