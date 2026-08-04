# Business layer — routing, sovereignty, and judgment gates

This is the layer that turns the agent pipeline into a consulting practice rather
than a code toy. The routing tiers are the contract; some controls are structural
today and the rest are still manual. (There is no roadmap to defer them to —
`docs/roadmap.md` was deleted in 2026-08; open items live in `docs/todo.md`.)

## Routing tiers (the data-sovereignty value prop)

| Tier | Route | Use for |
|---|---|---|
| **L — Sovereign** | Ollama on `mini` over the tailnet | Default; all client-confidential work |
| **G — Governed** | GitHub Copilot Pro+ with data retention disabled | Owner's own repos, or client work with written consent |
| **X — Personal** | xAI SuperGrok / Grok Build | Own repos, research, long autonomous runs — **never** client-confidential |
| **Z — Throwaway** | OpenCode Zen free models | OSS scaffolding only — **never** a client deliverable |

> `OLLAMA_BASE_URL=http://mini:11434` is set by the ser5 Hermes systemd units.
> Hermes's default model stays Tier L unless the operator runs `hermes model` or
> `hermes config set` themselves. xAI credentials come from SuperGrok OAuth
> (`hermes auth add xai-oauth`) or a vaulted `XAI_API_KEY`.

## 1. Sovereignty routing as a client guarantee

"Your code and data never leave hardware I control unless you approve a specific
governed exception." Be precise about what is enforced today:

- **Structural today:** Tier L routes to Ollama on `mini` over the tailnet.
- **Structural today:** Tier G is a separate GitHub Copilot surface with data
  retention disabled; use it for client work only with written consent.
- **Operator discipline today, Phase 4 enforcement planned:** default every client
  workspace to Tier L and require written approval before Tier G escalation.
- **Operator discipline today, Phase 4 enforcement planned:** Tier X and Tier Z
  never receive client-confidential context.
- **Operator discipline today, Phase 4 enforcement planned:** per-client pins force
  Tier L and refuse G/X/Z escalation.

The iPhone surface is Tailscale plus the tailnet-only Hermes dashboard at
`http://ser5:9119` for sovereign work; Telegram and Discord Hermes gateways are
personal/lab only because they route through third-party servers. Use the GitHub
mobile app to assign issues to the Copilot cloud agent.

## 2. Judgment gates via OpenSpec

Client-deliverable changes go through an OpenSpec proposal → approval **before**
agents execute. The agent team drafts the proposal, you approve the spec, then the
team builds to it. The orchestrator refuses to start implementation on a client repo
without an approved OpenSpec change ID. "Machines do the labor, you keep the
judgment."

## 3. Client isolation

- One opencode project **per client**.
- Planned: per-client subvaults under `clients/<name>/`, with a separate index and
  documented teardown. (Previously also called for a separate `QUALITY_LOOP_DATA`;
  quality-loop was deleted in 2026-08 and nothing replaced it.)
- Don't let one client's repo context bleed into another's session — separate
  workspaces, separate `AGENTS.md` where the engagement differs.

## Where this is wired

- **Gateway / remote surfaces:** `ser5/ansible/roles/hermes`.
- **Inference (Tier L):** `mini/ansible/roles/llamacpp` (two `llama-server`
  instances, `:8090` quality and `:8091` throughput). Note `ser5/ansible/roles/hermes`
  above is **not** Tier L today — it egresses to OpenRouter; see `docs/todo.md`.
- **Agent team:** [../opencode.json](../opencode.json) + [../AGENTS.md](../AGENTS.md).
- **Operator decision tree:** [../../docs/operating-manual.md](../../docs/operating-manual.md).
