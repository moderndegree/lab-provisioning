# Business layer — routing, sovereignty, and judgment gates

This is the layer that turns the agent pipeline into a consulting practice rather
than a code toy. The routing tiers from the runbook are enforced **once, centrally**
in the Hermes gateway (`ser5/ansible/roles/hermes`), not by per-session discipline.

## Routing tiers (the data-sovereignty value prop)

| Tier | Route | Use for |
|---|---|---|
| **L — Sovereign** | Ollama on `mini` (`oracle-32k` / `toolcaller-32k`) | Default for all client-confidential work |
| **B — Governed** | Claude via AWS Bedrock (zero-retention, prompt caching, Flex) | Vision-critical, frontier overflow, high-stakes deliverables |
| **Z — Throwaway** | OpenCode Zen free models | OSS / scaffolding only — **never** client deliverables |

> The Hermes config schema is your own; map this intent onto your real keys rather
> than inventing placeholders. The pieces known to be real are
> `HERMES_DOCKER_BINARY=podman` and `OLLAMA_BASE_URL=http://mini:11434` (both set by
> the ser5 `hermes` quadlet). Bedrock credentials resolve from the host AWS profile
> / IAM role via `AWS_REGION` — they never live in this repo.

## 1. Sovereignty routing as a client guarantee

"Your code and data never leave hardware I control unless you approve a specific
governed exception." Enforce it structurally:

- Default every client workspace to **Tier L**.
- **Tier B (Bedrock)** requires an explicit per-deliverable approval flag — and even
  then runs zero-retention with prompt caching for cost.
- **Tier Z** is firewalled from anything tagged client-confidential.
- **Per-client pin:** a client flag that forces Tier L and refuses B/Z escalation.

The remote surface (Cloudflare tunnel → `oc.moderndegree.com`) lets you drive the
whole thing from the Claude mobile app or Discord without exposing `mini` directly.

## 2. Judgment gates via OpenSpec

Client-deliverable changes go through an OpenSpec proposal → approval **before**
agents execute. The agent team drafts the proposal, you approve the spec, then the
team builds to it. The orchestrator refuses to start implementation on a client repo
without an approved OpenSpec change ID. "Machines do the labor, you keep the
judgment."

## 3. Client isolation

- One opencode project (and Ollama context scope) **per client**.
- Keep deliverable material in **per-client MinIO buckets**.
- Don't let one client's repo context bleed into another's session — separate
  workspaces, separate `AGENTS.md` where the engagement differs.

## Where this is wired

- **Gateway / tier enforcement:** `ser5/ansible/roles/hermes` (rootless Podman
  quadlet; `enable_hermes: true`).
- **Inference (Tier L):** `mini/ansible/roles/ollama` (four model variants).
- **Agent team:** [../opencode.json](../opencode.json) + [../AGENTS.md](../AGENTS.md).
