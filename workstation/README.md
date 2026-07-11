# workstation — opencode runtime config

This directory holds the **workstation (dev box)** half of the moderndegree local
agent stack. It is *not* provisioned by Ansible — `mini` (inference) and `ser5`
(Hermes gateway) are. These files are the opencode configuration you copy onto, or
symlink into, your development machine.

## Layout

| Path | Goes where on the workstation | Purpose |
|---|---|---|
| [opencode.json](opencode.json) | project root **or** `~/.config/opencode/` | Provider + model limits + the 9-agent team |
| [AGENTS.md](AGENTS.md) | project root | Injected at session start; the `@@RESULT` contract |
| [.moderndegree/prompts/](.moderndegree/prompts/) | project root | Per-agent system prompts referenced by `opencode.json` |
| [docs/business-layer.md](docs/business-layer.md) | reference | Tier L/B/Z routing, sovereignty, OpenSpec gates |

## The split that drives everything

Two warm base models on mini serve all nine agents — nothing else ever loads
(`OLLAMA_MAX_LOADED_MODELS=2`, `KEEP_ALIVE=-1`), and roles live in the agent
prompts rather than baked Modelfile variants:

- **`qwen3.6:27b-mtp-q4_K_M`** (dense, deep reasoning) — planner, architect,
  reviewer, security-auditor, coder, tester. Complex coding and hard analysis.
- **`qwen3.6:35b-a3b-mtp-q4_K_M`** (MoE, 3B active, fast) — build
  (orchestrator), devops, doc-writer. General work and tool dispatch.

Both run at their full native 256k window (set globally on mini —
`ollama_context_length` in `mini/ansible/group_vars/all.yml`); this config
matches `limit.context` so compaction budgets against the real number. Prefill
is the wall (~205 t/s), so the orchestrator prompt tells it to hand subagents
the *relevant* context, not the whole repo.

## Reasoning control (verified against Ollama 0.31.2)

Reasoning mode is per **agent**, set via `reasoningEffort` passthrough in
`opencode.json`: `"none"` on the orchestrator and devops (terse, deterministic
tool dispatch), unset (thinking on — the qwen3.6 default) everywhere else.

Two things that look like they work but **don't** through the `/v1` endpoint:
the `/think`/`/no_think` soft switches in prompts, and a `think: false` body
field. Both were tested and are ignored. `reasoning_effort: "none"` is the
only /v1 mechanism that disables thinking; native `/api/chat` honours
`think: false` for scripts.
