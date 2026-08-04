# moderndegree delivery contract

Every subagent ends its turn with exactly one block:

@@RESULT
status: PASS | FAIL | BLOCKED
summary: <one line>
handoff: <what the orchestrator should do next>
@@END

The orchestrator does not proceed past a gate until it receives PASS.
Reasoning agents (planner/architect/reviewer/security-auditor/doc-writer) never
call tools — they return analysis as text. Only the orchestrator and the
coder/tester/devops agents touch the filesystem or shell.

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

- `http://mini:8090/v1` — `qwen3.6-35b-a3b-mtp-q4_K_M` (MoE 35B-A3B, 3B active),
  4 slots, MTP on, 86-95 tok/s. Quality: planner, architect, reviewer,
  security-auditor, coder, tester, build (orchestrator), devops, doc-writer.
- `http://mini:8091/v1` — `gpt-oss-20b-MXFP4` (MoE 20B), 8 slots, 202 tok/s
  aggregate. Throughput: bulk fan-out and worker-shaped subtasks.

Both give 131072 context per slot, partitioned statically at startup — a single
session cannot exceed it. There is no residency or eviction to reason about any
more: llama-server holds its weights for the process lifetime. (The previous
"two warm slots" wording described Ollama, which is now stopped.)

Roles live in agent prompts, not baked model variants. Reasoning mode is set
per agent via `reasoningEffort` in `opencode.json` (`"none"` on the
orchestrator and devops for deterministic tool dispatch; everything else
thinks). The `/no_think` soft switch and a `think:false` body field do NOT
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

