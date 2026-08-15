# Operating manual

This lab is three things: the dev environment for a solo AI consulting practice, the demo floor for showing clients what good AI infrastructure looks like, and the reference implementation for rebuilding it cleanly. It is never client production hosting. The shape is deliberately boring: workstation drives, ser5 orchestrates, mini serves inference, iPhone initiates and supervises over Tailscale. Use the smallest tier that preserves confidentiality and gives the right quality; the point is results, not a headache.

## The decision tree

| Ask this first | Route | Tool | Tier | Why this, on this hardware |
|---|---|---|---|---|
| Is it client-confidential? | mini over the tailnet | opencode pointed at `http://mini:8090/v1` | L | Data stays on hardware the owner controls; mini is the sovereign inference appliance. |
| Am I at the desk on my own repos? | Windows workstation | GitHub Copilot CLI | G | Pro+ buys frontier models at a flat rate; the gaming PC is the primary cockpit. |
| Should it run for hours without me? | ser5, or GitHub cloud | Grok Build CLI on ser5, or assign a GitHub issue to Copilot cloud agent | X or G | ser5 survives disconnects via systemd; cloud sessions need no lab uptime. |
| Am I away from a computer? | ser5 | Hermes dashboard (`:9119`) from the phone | **Not L — routes to mini as of 2026-08-14 (verified), but 4 hosted credentials remain** | Tailscale gives private reach, but reach is not confidentiality. A default model is not a boundary while openrouter/opencode-zen/copilot/xai-oauth can still be reached. |
| Am I choosing between models or measuring quality? | ser5 driving mini | `packages/inference-bench` scripts | L | Measurements belong off mini; mini should only answer tokens. |

## Routing tiers

| Tier | Name | Endpoint | Use it for | Never use it for | Rationale |
|---|---|---|---|---|---|
| L | Sovereign | llama-server on mini over the tailnet (`:8090` quality, `:8091` throughput) | Client-confidential work, lab operations, private evals | Public chat gateways | Default tier. Data never leaves controlled hardware. |
| G | Governed | GitHub Copilot Pro+ with data retention disabled | Owner repos, infra, client work with written consent | Client-confidential work without consent | Frontier quality, flat-rate economics, strong ergonomics. |
| X | Personal | xAI SuperGrok / Grok Build | Own repos, research, long autonomous runs | Client-confidential material | Useful autonomy; not a confidentiality boundary. |
| Z | Throwaway | OpenCode Zen free models | OSS scaffolding only | Client deliverables | Fine for disposable glue; not where quality or privacy lives. |

## Devices

| Device | Role | Runs | Does not run | Why this, on this hardware |
|---|---|---|---|---|
| mini | Inference appliance | Headless Ubuntu 26.04, llama-server on `:8090`/`:8091`, Vulkan/RADV backend, tailnet-only; Ollama installed but stopped, started by hand only to try a model | Loops, experiments, dashboards, queues, client apps | Strix Halo has 128 GB unified LPDDR5X and ~110 GB usable GPU pool; protect it from anything that can crash or OOM. |
| ser5 | Always-on driver | Hermes, second brain (`/data/brain`), Grok Build CLI, Prometheus+Grafana, Open WebUI (opt-in), restic backups | Local models | Ryzen 7 5800H + 64 GB DDR4 is enough for orchestration; `/data` holds durable state. |
| workstation | Primary cockpit | Copilot CLI, opencode client, repo work | Local models | RTX 4080 Super 16 GB and 32 GB system RAM lose to mini for this fleet; drive mini instead. |
| iPhone | Remote control surface | Tailscale, Hermes dashboard, GitHub mobile, Grafana (Open WebUI only if `enable_openwebui` is turned on) | Bulk editing, production hosting | Starts work, reviews PRs, checks health; it is not the lab. |

## Models on mini

Strix Halo is memory-bandwidth-bound, not compute-bound. Decode speed tracks active parameters read per token, not total parameter count. The measured anchor is `qwen3.6-35b-a3b-mtp-q4_K_M` on `:8090`: 22 GB, MoE, 3B active, **106 tok/s single-stream and 109 tok/s aggregate at 4-way** (re-measured 2026-08-14 at MTP n-max 3, which is +14% over the n-max 1 used previously). `:8091` runs `qwen3.8-27b` — a DENSE 27B, deliberately — at ~25 tok/s sustained (replaced `nemotron-3.5-lightning` 2026-08-14, which was dominated on both axes). Consequence: MoE wins enormously here and dense is still the wrong default; `:8091` is the deliberate exception, viable only because MTP recovers 2.79x on it.

**What is actually served** (llama-server, measured 2026-08-04 on ROCm 7.14):

| Endpoint | Model | Size / shape | Speed | Use |
|---|---|---:|---:|---|
| `:8090` quality | `qwen3.6-35b-a3b-mtp-q4_K_M` | 22 GB, MoE 35B-A3B, 3B active | 106 tok/s c=1; 109 agg c=4 | THE DRIVER — every agent role except `deep`, plus all chat. MTP n-max 3. 262144 ctx/slot x 4. |
| `:8091` deep | `qwen3.8-27b-q4_K_M` | 19 GB + 3.2 GB MTP head, DENSE 27B hybrid | ~25 tok/s sustained | Hard problems only, called deliberately, never in a loop. ~4x slower. 262144 ctx/slot x 1; a second concurrent call queues. |

Context per slot: **262144 on BOTH endpoints** — each model's full native window (`:8090` 4 slots, `:8091` 1 slot). Swap a model by editing `llamacpp_instances`
in mini's `roles/llamacpp` — the units are named by role, not by model.

The table that used to live here listed `qwen3-coder-next`, `gpt-oss:120b`, the
Nemotron family and several bake-off candidates as "warm"/"heavy" tiers. That was
the Ollama residency model, which no longer exists: Ollama is stopped, nothing is
resident-by-eviction, and those tags are not served. Speeds marked `(est.)` there
were never measured. Treat any of them as a candidate to benchmark with
`packages/inference-bench`, not as an available route.


Serving policy is part of the model choice. **As of 2026-08 the serving path is
`llama-server` (mini's `roles/llamacpp`), not Ollama** — it measured ~3.5x faster
single-stream on the same model (86-95 vs 24.8 tok/s) because it can use the
GGUF's MTP head and its `--parallel` is tunable per workload. Ollama stays
installed for pulling and quickly trying a model by hand, but does not serve:
it and llama-server cannot both hold weights in 122 GiB. Start it only after
stopping an instance.

What actually serves (measured 2026-08-04, ROCm 7.14):

- `:8090` quality — `qwen3.6-35b-a3b-mtp-q4_K_M`, 4 slots, MTP n-max 3. 106 tok/s
  single-stream, 109 tok/s aggregate at 4-way.
- `:8091` deep — `qwen3.8-27b-q4_K_M`, 1 slot, MTP n-max 5 with a SEPARATE draft
  gguf (`-md`), f16 KV (q8_0 measured 11% SLOWER on long generations). ~25 tok/s sustained. Server pins
  `reasoning_effort=medium`; the model's own default (`xhigh`) returns an empty
  answer after ~400s.
- **262144 context per slot on BOTH endpoints** — each model's full native
  window — partitioned statically at startup (`-c` total / `-np` slots). A single
  chat can never exceed its endpoint's figure no matter how idle the box is;
  there is no per-request `num_ctx`.
- Prefill DEGRADES with depth: 1025 t/s at depth 0, 652 at 65536, 486 t/s
  measured on a real 137622-token request. A packed 262144 prompt therefore
  costs roughly 9-12 minutes before the first token. (The old "~205 t/s" figure
  in these docs was from the Ollama serving path and understates llama-server by
  about 2x.) The window is capacity, not a target — and prefix caching is what
  makes deep context usable, since a warm prefix skips this entirely.
- Do NOT raise total context blind: `-c 2097152` hung the amdgpu allocator hard
  enough to need a reboot. `llamacpp_ctx_warn` guards it.
- Thinking is disabled per request with `chat_template_kwargs: {enable_thinking:
  false}`. Budget for it when it is on — a 300-token cap was consumed entirely by
  reasoning, returning empty content.

Ollama's on-demand settings are now `context 32768 / parallel 1 / max_loaded 1 /
keep_alive 5m` — sized for trying one model by hand, not for serving.

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

What it is: the sovereign coding harness on the workstation, pointed at BOTH of mini's llama-server endpoints. The 10-agent team is split by concurrency: `build`, `planner`, `architect`, `coder` and `devops` on `:8090` (quality, MTP, critical path), and `reviewer`, `security-auditor`, `tester`, `doc-writer` and `librarian` on `:8091` (throughput, the parallel fan-out). `qa` runs on `:8090` (quality) and sequentially, after the batch — it is the final acceptance gate, validating the delivered system black-box against the done-when list rather than reviewing the diff. That split is sized to measurement — `:8090` peaks at 2 concurrent streams, `:8091` at 4 — see `../mini/AGENTS.md`. The `deep` escalation is hard reasoning on `:8090` with thinking left on; it replaces the old `heavy` agent, which pointed at `gpt-oss:120b` via Ollama and no longer resolves. The `research` escalation (xAI, Tier X, never client-confidential) needs `opencode auth login`, so the sovereign path is the failure-safe default.

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

Reach for it when: you want the phone surface or a gateway for personal, non-confidential work. **It does not default to Tier L.** Verified 2026-08-04: its configured `OLLAMA_BASE_URL=http://mini:11434` is dead (Ollama stopped) and the proxy serves 292 OpenRouter models instead, so traffic egresses to a third party. Treat it as Tier X at best until repointed. It can delegate coding to `grok` through `official/autonomous-ai-agents/grok`.

Start it:

```bash
systemctl --user start hermes-gateway hermes-proxy hermes-dashboard
```

Why this: Hermes is the bridge between small inputs and long-running work; it belongs on ser5, not mini.

### Second brain (`/data/brain` + AI Workstation)

What it is: Obsidian-compatible markdown vault for postmortems, decisions, and ACE playbooks. Seeded by the `brain` role (`enable_brain`). The **AI Workstation** app (sibling repo `ai-workstation`) is the primary UI and cortex MCP over the same path (`CORTEX_VAULT_DIR=/data/brain`).

Reach for it when: capturing lessons, browsing the knowledge graph, or letting agents search the vault via MCP.

Start it: run AI Workstation with `CORTEX_VAULT_DIR=/data/brain`, or open `/data/brain` in Obsidian. See [`brain.md`](brain.md).

## From the iPhone

1. Connect Tailscale iOS. Private names `mini` and `ser5` should resolve; nothing is exposed to the internet.
2. Open Hermes dashboard at `http://ser5:9119` in Safari.
3. Add it to Home Screen. This is the sovereign phone surface because it is tailnet-only.
4. For client-confidential work, do NOT use Hermes yet — its route to mini is configured but unverified and its OpenRouter fallback is still live (`docs/todo.md`). SSH to mini, or opencode against `http://mini:8090/v1`.
5. For personal and lab work, use the already-configured Hermes Telegram or Discord gateways for chat-driven task initiation.
6. Treat Telegram and Discord as third-party paths: notifications and personal lab work only, never client-confidential.
7. To start real coding from the couch, use GitHub mobile: assign an issue to the Copilot cloud agent, then review the PR.
8. Use Grafana on ser5 for host metrics when the lab feels slow.
9. Raw model chat from the phone needs Open WebUI on ser5, which ships **disabled** (`enable_openwebui: false` in `ser5/ansible/group_vars/all.yml`). Turn it on and re-provision if you want it; it is a convenience, not the cockpit.
10. If a phone path asks for secrets, stop and move to the workstation.

## Hard rules

- Never put a dense model on this hardware; MoE active-parameter count is what decodes fast.
- Never start Ollama while llama-server is running — they cannot both hold weights in 122 GiB.
- Never raise total context blind; `-c 2097152` hung the GPU allocator and needed a reboot.
- Embeddings live on ser5's CPU.
- Never send client-confidential material through Hermes until its four hosted credentials (openrouter, opencode-zen, copilot, xai-oauth) are gone. The mini route is verified as of 2026-08-14; the credentials are the remaining boundary (see `todo.md`).
- Client-confidential work never touches Telegram or Discord.
- Client-confidential work never leaves Tier L without explicit written consent.
- The lab never hosts client production.
- It is the dev environment, demo floor, and reference implementation.
- No AWS Bedrock.
- Speed figures marked `(est.)` stay marked until measured with `packages/inference-bench`.
- Adopt a model change only on a measured win.
- mini runs inference only: no loops, no experiments, nothing else ever.
- ser5 owns queues, dashboards, proxies, backups, and experiment state.
- The workstation drives; it does not host local models.
- The iPhone initiates and reviews; it does not become an ops exception.
- Keep prompts tight. 128k is capacity, not permission to paste the repo.
- Read the business context when needed: [`../workstation/docs/business-layer.md`](../workstation/docs/business-layer.md).

## When things are slow

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| First request is suddenly slow | Instance restarted and is reloading weights | `systemctl --user status llama-quality llama-deep` on mini | Wait out the load; llama-server holds weights for the process lifetime, so this is a restart, not eviction. |
| Nothing streams for minutes | Prefill wall, or thinking | Prompt size versus the slot window (262144 on :8090, 131072 on :8091); check whether `reasoning_content` is filling instead of `content` | Cut context, or disable thinking with `chat_template_kwargs: {enable_thinking: false}`. |
| A model is missing from the list | That instance is down, or Ollama got started and is contending | `curl mini:8090/v1/models`, `curl mini:8091/v1/models`, `systemctl is-active ollama` on mini | Restart the instance; stop Ollama — it and llama-server cannot both hold weights in 122 GiB. |
| Jobs queue behind each other | More concurrent requests than slots (4 on `:8090`, 8 on `:8091`) | `llamacpp:requests_deferred` and `llamacpp:requests_processing` in Prometheus | Let it queue, or send bulk fan-out to `:8091`. Raising slots lowers ctx/slot. |
| Throughput is far below the table | Estimate treated as fact | Re-measure with `packages/inference-bench` | Keep `(est.)` labels until measured; adopt only measured wins. |
| A service restarted but behaves like the old version | A stale `~/.config/systemd/user/<unit>.service` shadows the quadlet | `systemctl --user show <unit> -p FragmentPath --value` | Anything not under `.../systemd/generator/` is shadowed — delete it and `daemon-reload`. A clean converge does NOT catch this. |
