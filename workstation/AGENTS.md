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

## quality-loop (`qloop`) — free-text only

The lab quality authority is `qloop` (package `quality-loop` on ser5 agentlab).
Use it to **measure** strategies offline and to **polish free-text** when there
is no tool/test oracle. **Do not** run `qloop gate` on coder diffs — tests and
the reviewer are the code gate. See `.moderndegree/skills/quality-gate.md`.
