# workstation — opencode runtime config

This directory holds the **workstation (dev box)** half of the moderndegree local
agent stack. It is *not* provisioned by Ansible — `mini` (inference) and `ser5`
(Hermes gateway) are. These files are the opencode configuration you copy onto, or
symlink into, your development machine.

## Layout

| Path | Goes where on the workstation | Purpose |
|---|---|---|
| [opencode.json](opencode.json) | project root **or** `~/.config/opencode/` | Providers + model limits + the 9-agent team (plus `heavy` and `research` escalations) |
| [AGENTS.md](AGENTS.md) | project root | Injected at session start; the `@@RESULT` contract |
| [.moderndegree/prompts/](.moderndegree/prompts/) | project root | Per-agent system prompts referenced by `opencode.json` |
| [docs/business-layer.md](docs/business-layer.md) | reference | Tier L/G/X/Z routing, sovereignty, OpenSpec gates |
| [.moderndegree/skills/task-package.md](.moderndegree/skills/task-package.md) | project root | **Required** problem understanding + context packaging before subagent handoffs |
| [.moderndegree/skills/second-brain.md](.moderndegree/skills/second-brain.md) | project root | Postmortems + playbook promotion after misses (`/data/brain`) |
| [.moderndegree/skills/loop-budget.md](.moderndegree/skills/loop-budget.md) | project root | Anti-spin circuit breakers for agents |

## The split that drives everything

Two `llama-server` endpoints on mini serve all nine agents, and roles live in the
agent prompts rather than baked model variants:

- **`http://mini:8090/v1`** — `qwen3.6-35b-a3b-mtp-q4_K_M` (MoE 35B-A3B, 3B
  active), 4 slots, MTP on, 86-95 tok/s single-stream. All nine agents.
- **`http://mini:8091/v1`** — `gpt-oss-20b-MXFP4` (MoE 20B), 8 slots, 202 tok/s
  aggregate. Bulk fan-out and worker-shaped subtasks.

mini is memory-bandwidth-bound, not compute-bound. Decode speed tracks active
parameters per token, so MoE wins; a dense model is a mistake here.

Context is **131,072 per slot**, partitioned statically at startup (`-c` total /
`-np` slots) — a single session cannot exceed it however idle the box is, and
there is no per-request `num_ctx`. KV cost is ctx × slots, so raise one only by
lowering the other. Prefill runs ~205 t/s, so a packed 131k prompt costs ~10
minutes before the first token: the window is capacity, not a target.

(This section used to describe two *warm Ollama models* with residency and
eviction. Ollama is stopped as of 2026-08; llama-server holds its weights for
the process lifetime, so there is no eviction to reason about.)

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
