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

## TASK PACKAGE — context is the orchestrator's main job

Bad answers usually come from thin handoffs. Full rules:
`.moderndegree/skills/task-package.md`.

- Orchestrator **must** clarify the problem and build a **TASK PACKAGE** (goal,
  done-when, constraints, assumptions, pasted context excerpts) before dispatching
  any non-trivial subagent.
- Prefer **tools over user questions**; ask the user only for **blocking** unknowns.
- No-tools subagents work **only** from the package. Incomplete package → they
  return `BLOCKED` with exact gaps; orchestrator enriches and re-dispatches.
- Do not dump the whole repo or chat; paste relevant excerpts (prefill is expensive).
- `qloop` quality-gate polishes free-text **after** packaging — it does not gather repo context.

## Placement rule (the architectural backbone)

Two warm base models on mini serve every agent. mini is memory-bandwidth-bound:
decode speed tracks **active parameters read per token**, not total parameters.
That is why both warm slots are MoE, and why **no agent may introduce a dense
model or a third resident model**:

- `qwen3-coder-next:latest` (MoE 80B-A3B, 3B active) — depth slot:
  planner, architect, reviewer, security-auditor, coder, tester.
- `qwen3.6:35b-a3b-mtp-q4_K_M` (MoE 35B-A3B, 3B active) — driver slot:
  build (orchestrator), devops, doc-writer.

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

## Second brain (memory)

Lab memory is a markdown vault at `/data/brain` on ser5 (Obsidian-compatible).
After painful misses, draft a postmortem under `notes/postmortems/` and promote
durable rules into ACE playbooks — see `.moderndegree/skills/second-brain.md`.
Do not treat unreviewed agent drafts as ground truth; no client secrets in the
personal vault.

## quality-loop (`qloop`) — automated free-text gate

The lab quality authority is `qloop` (package `quality-loop`). Full rules live
in `.moderndegree/skills/quality-gate.md` — the orchestrator **must** follow
that decision tree:

- **Code** (implement, diff, tests) → never `qloop`; tools + reviewer + `@@RESULT`.
- **Multi-constraint free-text** (risks, proposals, runbooks, actionable plans,
  extraction wording) → orchestrator **must** run `qloop gate` before the final
  user-facing answer (or via `devops`).
- Skip only when the skill says so (`GATE: skip`, short fact, pure plumbing).
- Warm models only (`general`/`coder`). Never heavy/judge/scout in the gate.
- Prose subagents set `handoff: run quality-gate …` when their draft is user-facing.
