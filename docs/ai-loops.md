# AI loop strategies on the lab

How the two machines run self-improving AI loops — the architecture, why each
piece sits where it does, and the runbook for a typical experiment.

## Architecture

```
┌────────────────────────── tailnet (Tailscale) ──────────────────────────┐
│                                                                          │
│  mini (MS-S1 Max, Strix Halo, 128 GB unified)     ser5 (SER5, 64 GB)     │
│  ── inference appliance, nothing else ──          ── always-on driver ── │
│  Ollama :11434 (OpenAI-compatible /v1)            agentlab: loopkit      │
│  two warm base models, 256k windows, no             loops · playbooks    │
│  baked prompts (max_loaded=2, keep_alive=-1):       evals · SQLite runs  │
│    qwen3.6:27b-mtp-q4_K_M      dense — complex      systemd user jobs    │
│                                coding + reasoning hermes gateway         │
│    qwen3.6:35b-a3b-mtp-q4_K_M  MoE (3B active) —  Prometheus + Grafana   │
│                                general, fast      restic backups (timer) │
│                                                                          │
│  workstation ── opencode 9-agent team (interactive coding) ──────────────│
└──────────────────────────────────────────────────────────────────────────┘
```

Division of labor:

- **mini** stays a pure inference appliance. Loops never run on it — a crashed
  experiment must never take down the model server, and unified-memory OOM is
  mini's one soft spot. Its provisioning (`mini/`) is deliberately frozen:
  pinned ROCm/Vulkan stack, baked model variants.
- **ser5** drives every loop. It is always on, cheap to run, and losing it
  loses nothing (experiment state lives on `/data`, snapshotted by restic).
  Long-horizon runs go through `systemd --user` template units so they survive
  SSH disconnects.
- **Two warm base models, roles in prompts.** Exactly two models stay resident
  on mini, with no baked system prompts — agent roles live in the prompts that
  loopkit and opencode send, so one loaded model serves many agents. Reasoning
  is a per-request toggle (`reasoning_effort: "none"` on /v1). loopkit's
  aliases (`general`, `coder`, `scout`) encode the routing.

## The loop stack (packages/loopkit)

Three composable layers, all producing scored, persisted traces:

1. **Strategies** (`loops.py`) — `single` (baseline), `refine`
   (generate→critique→revise), `best_of_n` (sample+judge). This is test-time
   compute scaling: mini's ~40–100 tok/s is cheap; spend it.
2. **Evolving context** (`playbook.py`) — ACE-style playbooks: numbered tactic
   bullets injected as system context, updated by a reflector model through
   incremental ADD/UPDATE/REMOVE deltas. Delta ops (not rewrites) are what
   prevents context collapse; the dedup + cap keeps it bounded. Playbooks are
   plain markdown under `/data/agentlab/playbooks` — version them with git if
   they get valuable.
3. **Improvement data** (`star.py`) — STaR bootstrapping: run a suite, keep
   correct traces, rationalize failures with an answer hint, emit chat-format
   JSONL to `/data/agentlab/datasets`. This is the training-data half of
   self-improvement; actual LoRA runs are future work (see below).

Everything is measured (`evals.py` + `storage.py`): every run lands in
`/data/agentlab/runs.db` (SQLite) with full traces in
`/data/agentlab/traces/*.jsonl`. `loopkit stats` compares strategies on score
*and* token cost.

## Runbook: a typical experiment

```bash
ssh ser5
# 1. write or pick a suite
vim /data/agentlab/suites/mytask.jsonl     # {"id","prompt","expected","match"} per line

# 2. baseline first — always
loopkit eval /data/agentlab/suites/mytask.jsonl --strategy single --worker general

# 3. the loop under test, with an evolving playbook
loopkit eval /data/agentlab/suites/mytask.jsonl --strategy refine \
        --playbook /data/agentlab/playbooks/mytask.md --reflect

# 4. compare
loopkit stats

# 5. long-horizon / unattended: wrap steps 2–4 in a job script
cp /data/agentlab/jobs/example-eval.sh /data/agentlab/jobs/mytask.sh && vim !$
systemctl --user start agentlab-run@mytask
journalctl --user -u agentlab-run@mytask -f
```

Model routing guidance (matches the workstation agent stack):

| Call | Use | Why |
|------|-----|-----|
| candidate generation / judging / reflection | `general` (qwen3.6 35B-A3B MoE) | 3B active params — fast to sample, thinks by default |
| complex coding, deep reasoning, stuck tasks | `coder` (qwen3.6 27B dense) | the quality escalation; slower per token |
| throwaway smoke tests only | `scout` (nano 4B) | loading it evicts one of the warm pair — never in loops |

Both warm models have the full 256k window; there is no separate long-context
variant. Prefill cost is the guardrail: pass what the task needs, not the corpus.

## Operational guardrails

- **One interactive stream at a time on mini** (`ollama_num_parallel: 1`).
  Loops are sequential by design; don't parallelize suites against mini —
  queueing is fine, OOM is not.
- **Watch prefill.** Long contexts pay a multi-minute prefill at ~205 t/s.
  Prefer many small calls (refine rounds) over one giant-context call unless
  the task truly needs `batch`.
- **Reliability chain:** experiment state on `/data` → restic snapshots daily
  (`enable_backups`) → node metrics in Grafana (`enable_observability`).
  `runs.db` is the lab notebook; treat it as append-only.
- **Security posture is inherited:** everything is tailnet-only (UFW default
  deny), loopkit adds no listening ports, and no secrets — the Ollama endpoint
  is unauthenticated *inside* the tailnet only.

## Future work (deliberate deferrals)

- **LoRA/QLoRA on the Strix Halo iGPU** — gfx1151 training support (PyTorch
  ROCm) is still community-grade; loopkit already emits SFT-ready datasets, so
  training plugs in the day the stack is trustworthy. Until then, fine-tune in
  a rented GPU hour with the bootstrapped data if a result matters.
- **llama-server/vLLM backend** — standalone llama.cpp on Vulkan roughly
  doubles decode throughput vs Ollama on this box (~98–103 t/s on Qwen3-30B).
  Worth a gated `llamacpp` role on mini if loop throughput becomes the
  bottleneck; loopkit only needs `LOOPKIT_BASE_URL` pointed at the new port.
- **LLM-level dashboards** — export `runs.db` aggregates to Prometheus
  (textfile collector) once there are enough runs to trend.
