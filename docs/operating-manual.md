# Operating manual

This lab is three things: the dev environment for a solo AI consulting practice, the demo floor for showing clients what good AI infrastructure looks like, and the reference implementation for rebuilding it cleanly. It is never client production hosting. The shape is deliberately boring: workstation drives, ser5 orchestrates, mini serves inference, iPhone initiates and supervises over Tailscale. Use the smallest tier that preserves confidentiality and gives the right quality; the point is results, not a headache.

## The decision tree

| Ask this first | Route | Tool | Tier | Why this, on this hardware |
|---|---|---|---|---|
| Is it client-confidential? | mini over the tailnet | opencode pointed at `http://mini:11434/v1` | L | Data stays on hardware the owner controls; mini is the sovereign inference appliance. |
| Am I at the desk on my own repos? | Windows workstation | GitHub Copilot CLI | G | Pro+ buys frontier models at a flat rate; the gaming PC is the primary cockpit. |
| Should it run for hours without me? | ser5, or GitHub cloud | Grok Build CLI on ser5, or assign a GitHub issue to Copilot cloud agent | X or G | ser5 survives disconnects via systemd; cloud sessions need no lab uptime. |
| Am I away from a computer? | ser5 | Hermes from the phone | L for dashboard, personal-only for chat gateways | Tailscale gives private reach; Hermes is already running and phone-shaped. |
| Am I choosing between models or measuring quality? | ser5 driving mini | qloop (quality-loop) | L | Measurements belong off mini; mini should only answer tokens. |

## Routing tiers

| Tier | Name | Endpoint | Use it for | Never use it for | Rationale |
|---|---|---|---|---|---|
| L | Sovereign | Ollama on mini over the tailnet | Client-confidential work, lab operations, private evals | Public chat gateways | Default tier. Data never leaves controlled hardware. |
| G | Governed | GitHub Copilot Pro+ with data retention disabled | Owner repos, infra, client work with written consent | Client-confidential work without consent | Frontier quality, flat-rate economics, strong ergonomics. |
| X | Personal | xAI SuperGrok / Grok Build | Own repos, research, long autonomous runs | Client-confidential material | Useful autonomy; not a confidentiality boundary. |
| Z | Throwaway | OpenCode Zen free models | OSS scaffolding only | Client deliverables | Fine for disposable glue; not where quality or privacy lives. |

## Devices

| Device | Role | Runs | Does not run | Why this, on this hardware |
|---|---|---|---|---|
| mini | Inference appliance | Headless Ubuntu 26.04, Ollama on `:11434`, Vulkan/RADV backend, tailnet-only | Loops, experiments, dashboards, queues, client apps | Strix Halo has 128 GB unified LPDDR5X and ~110 GB usable GPU pool; protect it from anything that can crash or OOM. |
| ser5 | Always-on driver | Hermes, quality-loop/agentlab, second brain (`/data/brain`), Grok Build CLI, Prometheus+Grafana, restic backups | Local models | Ryzen 7 5800H + 64 GB DDR4 is enough for orchestration; `/data` holds durable state. |
| workstation | Primary cockpit | Copilot CLI, opencode client, repo work | Local models | RTX 4080 Super 16 GB and 32 GB system RAM lose to mini for this fleet; drive mini instead. |
| iPhone | Remote control surface | Tailscale, Hermes dashboard, GitHub mobile, Grafana (Open WebUI only if `enable_openwebui` is turned on) | Bulk editing, production hosting | Starts work, reviews PRs, checks health; it is not the lab. |

## Models on mini

Strix Halo is memory-bandwidth-bound, not compute-bound. Decode speed tracks active parameters read per token, not total parameter count. The measured anchor is `qwen3.6:35b-a3b-mtp-q4_K_M`: 22 GB, MoE, 3B active, 70-80 t/s, implying ~185 GB/s effective bandwidth, about 86% of the ~215 GB/s ceiling. Consequence: MoE models win enormously here, and a dense model in a warm slot is a mistake.

| Model | Size / shape | Speed | Residency | Use | Bandwidth rationale |
|---|---:|---:|---|---|---|
| `qwen3.6:35b-a3b-mtp-q4_K_M` | 22 GB, MoE 35B-A3B, 3B active | 70–80 t/s (measured) | Warm driver slot | Orchestrator, devops, doc-writer, qloop judge/reflector | The anchor: fast because only ~3B active params are read per token. |
| `qwen3-coder-next:latest` | 51 GB, MoE 80B-A3B hybrid Gated-DeltaNet, 512 experts / 10 active, 3B active | ~35-50 t/s (est.) | Warm depth slot | Coder, tester, planner, architect, reviewer, security-auditor | Replaces dense 27B: much stronger coding while keeping 3B active. |
| `glm-4.7-flash:latest` | 19 GB, MoE 30B-A3B, 3B active | ~75-90 t/s (est.) | Challenger, not resident today | Driver-slot bake-off | Similar active size to the driver; measure before swapping. |
| `gpt-oss:120b` | 65 GB, MoE 117B-A5.1B, 5.1B active | ~30 t/s (community-measured) | Heavy tier | Strongest general reasoning that fits 128 GB | Loads by evicting a warm model; useful, not resident. |
| `nemotron-cascade-2:latest` | 24 GB, Mamba2-Transformer MoE 30B-A3B, ~3.6B active | ~60-80 t/s (est.) | Heavy tier | Math/algorithm escalation; independent judge for `best_of_n` | Different model family beats a model grading its own samples. |
| `nemotron3:33b` | 27 GB, Nemotron-3-Nano-30B-A3B family | ~55-80 t/s (est.) | Bench-off | Long-context candidate | 1M context and Mamba layers make long context cheap; keep one winner. |
| `nemotron-3-nano:latest` | 24 GB, same family, different quant | ~55-80 t/s (est.) | Bench-off | Long-context candidate | Compare against `nemotron3:33b`; do not keep both by habit. |
| `qwen3.6:27b-mtp-q4_K_M` | 17 GB, dense 27B | ~11-15 t/s (est.) | Demoted rollback | Only if coder-next fails | Dense reads the whole model per token; roughly 6x slower than the driver. |
| `qwen3.6:35b-a3b-mtp-q8_0` | 38 GB, q8 MoE | About half q4 throughput | Retirement candidate | Quality bake-off only | q8 spends bandwidth for marginal quality gain. |
| `qwen3.6:27b-mtp-q8_0` | 29 GB, q8 dense | About half q4 throughput | Retirement candidate | Quality bake-off only | Dense plus q8 is the wrong direction on this box. |
| `nemotron-3-nano:4b` | 2.8 GB | 150+ t/s | `scout` alias | Throwaway smoke tests only | Even tiny models evict a warm slot; never leave it resident. |

Serving policy is part of the model choice:

- Exactly two resident models: `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=-1`.
- Warm pair today: driver `qwen3.6:35b-a3b-mtp-q4_K_M` plus depth `qwen3-coder-next:latest`.
- Global context window is 131072, not 262144.
- Weights 51+22 = 73 GB; q8_0 KV at 2-way parallel adds ~22 GB, landing near 95 GB of the ~110 GB pool.
- KV cost is context × parallel, so 128k at 2-way is the same budget as 64k at 4-way. Raise one only by lowering the other.
- 256k never fit this pair and was never usable anyway: prefill is ~205 t/s, so a packed 256k prompt costs ~21 minutes before the first token.
- 128k costs ~10 minutes worst case; the window is capacity, not a target.
- `OLLAMA_NUM_PARALLEL=2`: two concurrent streams, then queue.
- `/v1` cannot set `num_ctx` per request, and Modelfile `num_ctx` below native is ignored; the global env var is the control point.
- `reasoning_effort: "none"` is the only verified way to disable thinking on `/v1` in Ollama 0.31.2.
- `/no_think` and `think: false` are ignored on `/v1`; native `/api/chat` honors `think: false`.

## The harnesses

### GitHub Copilot CLI

What it is: this app on the Windows workstation, attached to the repo and the diff. It is the daily driver for the owner's own repos and infra.

Reach for it when: you are at the desk, the work is yours, and Pro+ frontier models plus cloud sessions beat babysitting local agents.

Start it:

```powershell
copilot
```

Why this: the workstation is the cockpit, not the model host; 32 GB RAM and a 16 GB RTX 4080 Super lose to mini on every local-inference axis.

### opencode

What it is: the sovereign coding harness on the workstation, pointed at `http://mini:11434/v1`. The default 9-agent team is `build` orchestrator, planner, architect, reviewer, security-auditor, coder, tester, devops, and doc-writer — all on mini. Two opt-in escalations sit alongside it: `heavy` (`gpt-oss:120b`, still Tier L but evicts a warm model) and `research` (xAI, Tier X, never client-confidential). Both need `opencode auth login` before they work, so the sovereign path is the failure-safe default.

Reach for it when: the task is client-confidential, the repo is local, and the answer must stay Tier L. Config lives in `../workstation/opencode.json`; delivery contract is in [`../workstation/AGENTS.md`](../workstation/AGENTS.md).

Start it:

```powershell
opencode
```

Why this: roles live in prompts, not baked model variants, so one loaded model can serve many agents without wasting residency.

### Grok Build

What it is: `grok` on ser5, installed at `~/.grok/bin` by the workstation Ansible role. Auth is `grok login` with SuperGrok.

Reach for it when: it is your own repo or research, it can run autonomously, and it may need to survive your laptop disconnecting. Never put client-confidential material here.

Start it:

```bash
grok -p "implement the issue and open a PR"
```

Why this: ser5 is the always-on driver; Grok gives long autonomous coding runs without consuming mini residency.

### Hermes

What it is: NousResearch Hermes on ser5 with `HERMES_HOME=/data/services/hermes`. It has three surfaces: messaging gateway, OpenAI-compatible proxy on `:8645`, and web dashboard on `:9119`.

Reach for it when: you want the phone surface, a gateway, or a proxy that defaults to Tier L via `OLLAMA_BASE_URL=http://mini:11434`. It can delegate coding to `grok` through `official/autonomous-ai-agents/grok`.

Start it:

```bash
systemctl --user start hermes-gateway hermes-proxy hermes-dashboard
```

Why this: Hermes is the bridge between small inputs and long-running work; it belongs on ser5, not mini.

### Second brain (`/data/brain`)

What it is: Obsidian-compatible markdown vault for postmortems, decisions, and ACE playbooks (often symlinked from agentlab). Seeded by the `brain` role (`enable_brain`).

Reach for it when: capturing lessons after bad answers or thin handoffs; browsing lab memory in Obsidian.

Start it: open `/data/brain` as an Obsidian vault (sync/mount from workstation). See [`brain.md`](brain.md).

### quality-loop (`qloop`)

What it is: the lab quality authority in `packages/quality-loop` (`qloop`), deployed to `/data/agentlab` by the `agentlab` role. Strategies are `single`, `refine`, and `best_of_n`, plus ACE playbooks, STaR dataset bootstrapping, and an interactive `gate` for free-text polish.

Reach for it when: choosing models, comparing prompts, measuring quality, running eval loops, or polishing Hermes free-text (no tool oracle). Do **not** use it to re-review OpenCode code diffs. Every run lands in `/data/agentlab/runs.db`; see [`ai-loops.md`](ai-loops.md) and [`../packages/quality-loop/README.md`](../packages/quality-loop/README.md).

Start it:

```bash
make qloop-matrix
qloop summary
# free-text gate (ser5):
/data/agentlab/jobs/quality-gate.sh --task "…" --strategy refine
```

Why this: `single` is always the baseline; `refine` and `best_of_n` spend cheap local tokens only after the baseline exists. Interactive gate uses warm models only. Aliases are `general`, `coder`, `heavy`, `judge`, and `scout`.

## From the iPhone

1. Connect Tailscale iOS. Private names `mini` and `ser5` should resolve; nothing is exposed to the internet.
2. Open Hermes dashboard at `http://ser5:9119` in Safari.
3. Add it to Home Screen. This is the sovereign phone surface because it is tailnet-only.
4. For client-confidential work, use Hermes dashboard only, routed to Tier L on mini.
5. For personal and lab work, use the already-configured Hermes Telegram or Discord gateways for chat-driven task initiation.
6. Treat Telegram and Discord as third-party paths: notifications and personal lab work only, never client-confidential.
7. To start real coding from the couch, use GitHub mobile: assign an issue to the Copilot cloud agent, then review the PR.
8. Use Grafana on ser5 for host metrics when the lab feels slow.
9. Raw model chat from the phone needs Open WebUI on mini, which ships **disabled** (`enable_openwebui: false` in `mini/ansible/group_vars/all.yml`). Turn it on and re-provision if you want it; it is a convenience, not the cockpit.
10. If a phone path asks for secrets, stop and move to the workstation.

## Hard rules

- Never put a dense model in a warm slot on this hardware.
- Never a third resident model.
- Embeddings live on ser5's CPU.
- Heavy models are scheduled evictions, not residents.
- Client-confidential work never touches Telegram or Discord.
- Client-confidential work never leaves Tier L without explicit written consent.
- The lab never hosts client production.
- It is the dev environment, demo floor, and reference implementation.
- No AWS Bedrock.
- Speed figures marked `(est.)` stay marked until qloop measures them.
- Adopt a model change only on a measured win.
- mini runs inference only: no loops, no experiments, nothing else ever.
- ser5 owns queues, dashboards, proxies, backups, and experiment state.
- The workstation drives; it does not host local models.
- The iPhone initiates and reviews; it does not become an ops exception.
- Keep prompts tight. 128k is capacity, not permission to paste the repo.
- Read the business context when needed: [`../workstation/docs/business-layer.md`](../workstation/docs/business-layer.md) and [`roadmap.md`](roadmap.md).

## When things are slow

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| First request is suddenly slow and logs show loading | Model got evicted | `ollama ps` on mini | Stop using `scout` or heavy models casually; reload the warm pair. |
| Nothing streams for minutes | Prefill wall | Prompt size versus the 128k window | Cut context, summarize, or split into smaller calls. |
| Driver or coder is missing | Heavy model resident | `ollama ps`; look for `gpt-oss:120b` or Nemotron heavy tier | Treat heavy as scheduled eviction; finish it, then restore the warm pair. |
| Two jobs run, the third waits | Two-way parallel queueing | `OLLAMA_NUM_PARALLEL=2` and active clients | Let it queue, or move non-urgent evals to later. Do not raise residency. |
| Throughput is far below the table | Estimate treated as fact | Compare against qloop runs | Keep `(est.)` labels until measured; adopt only measured wins. |
