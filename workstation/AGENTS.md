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

Two warm base models on mini serve every agent — memory is the constraint, so
**no agent may introduce a third model**. Placement is by task depth, not tool
capability (both models handle tools and a 256k window):

- `qwen3.6:27b-mtp-q4_K_M` (dense) — complex coding and deep reasoning:
  planner, architect, reviewer, security-auditor, coder, tester.
- `qwen3.6:35b-a3b-mtp-q4_K_M` (MoE, 3B active — fast) — general tasks and
  orchestration: build (orchestrator), devops, doc-writer.

Roles live in agent prompts, not baked model variants. Reasoning mode is set
per agent via `reasoningEffort` in `opencode.json` (`"none"` on the
orchestrator and devops for deterministic tool dispatch; everything else
thinks). The `/no_think` soft switch and a `think:false` body field do NOT
work through Ollama's `/v1` endpoint — never rely on them.

## Client-deliverable guardrails

- Default every client workspace to **Tier L (Sovereign)** — Ollama on `mini`.
- **Tier B (Bedrock)** requires an explicit per-deliverable approval flag; even then
  it runs zero-retention with prompt caching.
- **Tier Z (OpenCode Zen free)** is firewalled from anything tagged client-confidential.
- The orchestrator refuses to start implementation on a client repo without an
  approved OpenSpec change ID.
