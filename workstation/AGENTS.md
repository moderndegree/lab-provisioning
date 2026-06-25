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

An agent goes on Qwen (`oracle-32k`) **only if it needs zero tools**. The moment an
agent must read a file, run bash, edit, or fetch, it goes on Nemotron
(`toolcaller-32k`). Qwen is text-only and breaks if handed a tools array; Nemotron
is the sole tool-caller. Reasoning mode is a property of the model, not the agent:
oracle variants are reasoning-on, the tool-caller is reasoning-off.

## Client-deliverable guardrails

- Default every client workspace to **Tier L (Sovereign)** — Ollama on `mini`.
- **Tier B (Bedrock)** requires an explicit per-deliverable approval flag; even then
  it runs zero-retention with prompt caching.
- **Tier Z (OpenCode Zen free)** is firewalled from anything tagged client-confidential.
- The orchestrator refuses to start implementation on a client repo without an
  approved OpenSpec change ID.
