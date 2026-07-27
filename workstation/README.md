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
| [.moderndegree/skills/quality-gate.md](.moderndegree/skills/quality-gate.md) | project root | **Required** free-text polish via `qloop gate` (never code diffs) |

## The split that drives everything

Two warm MoE base models on mini serve all nine agents — nothing else ever
loads (`OLLAMA_MAX_LOADED_MODELS=2`, `KEEP_ALIVE=-1`), and roles live in the
agent prompts rather than baked Modelfile variants:

- **`qwen3-coder-next:latest`** (MoE 80B-A3B, 3B active, depth) — planner,
  architect, reviewer, security-auditor, coder, tester. Complex coding and hard
  analysis.
- **`qwen3.6:35b-a3b-mtp-q4_K_M`** (MoE 35B-A3B, 3B active, driver) — build
  (orchestrator), devops, doc-writer. General work and tool dispatch.

mini is memory-bandwidth-bound, not compute-bound. Decode speed tracks active
parameters per token, so MoE wins; a dense model in a warm slot is a mistake.
The global context window is now **131,072**. KV cost is context × parallel, so
128k at `OLLAMA_NUM_PARALLEL=2` is the same KV budget as 64k at 4-way: the
51 GB + 22 GB warm pair plus ~22 GB of q8_0 KV lands near 95 GB of the ~110 GB
GPU pool. The trade is concurrency — a third simultaneous request queues. 256k
does not fit this pair and was never operationally useful at ~205 t/s prefill.

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

## quality-loop (`qloop`) on the workstation

The OpenCode orchestrator **must** run `qloop gate` on multi-constraint free-text
(see skill + orchestrator prompt). Install once so agents can find the binary:

```bash
make qloop-venv
# put on PATH for agent sessions, e.g.:
export PATH="$PWD/.venv/bin:$PATH"
.venv/bin/qloop models
```

See [../packages/quality-loop/README.md](../packages/quality-loop/README.md) and
[../docs/ai-loops.md](../docs/ai-loops.md).

## Reasoning control (verified against Ollama 0.31.2)

Reasoning mode is per **agent**, set via `reasoningEffort` passthrough in
`opencode.json`: `"none"` on the orchestrator and devops (terse, deterministic
tool dispatch), unset (thinking on for the Qwen stack) everywhere else.

Two things that look like they work but **don't** through the `/v1` endpoint:
the `/think`/`/no_think` soft switches in prompts, and a `think: false` body
field. Both were tested and are ignored. `reasoning_effort: "none"` is the
only /v1 mechanism that disables thinking; native `/api/chat` honours
`think: false` for scripts.
