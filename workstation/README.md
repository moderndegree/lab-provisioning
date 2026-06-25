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

`oracle-32k` (Qwen, text-only, reasoning-on) hosts the tool-free reasoning agents
(planner, architect, reviewer, security-auditor, doc-writer). `toolcaller-32k`
(Nemotron, sole tool-caller, reasoning-off) hosts the orchestrator and the
coder/tester/devops agents. Two async escalations — `oracle-batch-192k` (whole-repo
reads) and `heavy-64k` (120B hard calls) — are routed by the orchestrator and
accepted at minutes-per-response.

All four model variants are built on `mini` by the `ollama` role
(`mini/ansible/roles/ollama`, `ollama_models` in group_vars). This config just
points opencode at `http://mini:11434/v1` and matches each model's `limit.context`
to the baked window so compaction budgets against the right number.

## Verify before wiring all five oracle agents

`permission: "deny"` blocks tool *execution*, but Qwen breaks on the tools array's
*presence*. Test one oracle agent end to end first. If you see a "tools not
supported" / JSON-parse error, fall back to the array-stripping `tools: {...false}`
map on that agent (see the runbook §4.3).
