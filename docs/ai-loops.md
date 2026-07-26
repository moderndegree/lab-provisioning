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
│  two warm MoE base models, 128k global ctx, no      loops · playbooks    │
│  baked prompts (max_loaded=2, keep_alive=-1):       evals · SQLite runs  │
│    qwen3-coder-next:latest     MoE (3B active) —   systemd user jobs    │
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
  is a per-request toggle (`reasoning_effort: "none"` on /v1). The warm slots
  are `qwen3-coder-next:latest` and `qwen3.6:35b-a3b-mtp-q4_K_M`; both are MoE
  because mini is bandwidth-bound and decode speed tracks active parameters
  read per token, not total parameters. loopkit's aliases (`general`, `coder`,
  `heavy`, `judge`, `scout`) encode the routing.

For the day-to-day operating policy, escalation tiers, and model inventory, see
[`operating-manual.md`](operating-manual.md).

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
| candidate generation / judging / reflection | `general` (`qwen3.6:35b-a3b-mtp-q4_K_M`) | 70–80 t/s (measured); 3B active params — fast to sample, thinks by default |
| complex coding, deep reasoning, stuck tasks | `coder` (`qwen3-coder-next:latest`) | ~35–50 t/s (est.); the quality escalation and depth slot |
| off-hours hardest general reasoning | `heavy` (`gpt-oss:120b`) | ~30 t/s (community-measured); evicts a warm model, so scheduled jobs only |
| math/algorithm escalation, independent best-of-N judging | `judge` (`nemotron-cascade-2:latest`) | ~60–80 t/s (est.); different model family beats self-grading, but evicts a warm model |
| throwaway smoke tests only | `scout` (`nemotron-3-nano:4b`) | 150+ t/s; loading it evicts one of the warm pair — never in loops |

Global context is 131072. The previous 256k target does not fit the new warm pair
and was never operationally sane: at ~205 t/s prefill, a packed 256k prompt
costs ~21 minutes before the first token; 128k is a ~10 minute worst case.
The /v1 endpoint cannot set `num_ctx` per request, so the global env var is the
control point. Pass what the task needs, not the corpus.

## Phase 1 baseline matrix and the quarterly bake-off

The Phase 1 suites live in `packages/loopkit/suites/`: `extraction.jsonl`,
`citation-qa.jsonl`, `structured-output.jsonl`, `coding.jsonl` (plus the
original `smoke.jsonl` for wiring checks, not a benchmark). Three extra match
modes support them beyond exact/contains/regex/numeric:

- `json_schema` — the answer's JSON (fenced ```json``` block, ANSWER: line, or
  raw text, in that order) is validated against the task's `schema` field with
  a minimal stdlib validator (type/required/properties/items/enum).
- `test` — the answer's fenced ```python``` code block is executed alongside
  the task's `tests` field (plain asserts) in a subprocess with a timeout;
  scored 1.0 iff it exits clean. This runs model-generated code — sandboxed to
  a throwaway temp dir, never on mini.

Run the full 4-suite x 3-strategy x 2-worker matrix (single always establishes
the baseline per combo):

```bash
make loopkit-matrix                    # from the repo root; needs mini reachable
loopkit stats --matrix                 # one row per suite x strategy x worker (latest run)
loopkit summary                        # one-page markdown: matrix + best-strategy deltas
loopkit summary --out quality.md       # write it to a file instead of stdout
```

**Quarterly bake-off ritual** — when a new candidate base model shows up:

1. Pull the candidate onto mini as a third, temporary model (or swap it into an
   already-idle slot) — never disturb the warm pair mid-experiment.
2. Run the same 4 suites against it: `loopkit eval <suite> --strategy single
   --worker <candidate-tag>` (repeat for `refine`/`best_of_n` if the single
   baseline looks competitive).
3. `loopkit summary` — compare the candidate's row-for-row scores and token
   cost against the current `general`/`coder` matrix.
4. Adopt only on a measured win (better mean score at comparable or better
   tokens/s); otherwise the candidate is rejected and the incumbent stays.
   Record the decision (a line in this file or a run-store note) — the
   two-model policy holds, only the *occupants* change.
5. Re-run `make loopkit-matrix` fully once an occupant changes, so the baseline
   matrix always reflects who's actually resident.

Current cycle: `glm-4.7-flash:latest` is the live challenger against incumbent
`qwen3.6:35b-a3b-mtp-q4_K_M` for the driver slot; settle it on measured local
data, not published benchmarks. The depth-slot swap from dense 27B to
`qwen3-coder-next:latest` landed on published SWE-bench wins and still needs
local confirmation in the same matrix.

## Operational guardrails

- **Up to two concurrent streams on mini** (`ollama_num_parallel: 2`).
  At 128k global context, the warm pair plus q8_0 KV is ~95 GB of the ~110 GB
  usable GPU pool. KV cost is context × parallel, so the 128k window was bought
  by halving concurrency. Keep suites conservative and let overflow queue rather
  than pushing the node toward OOM.
- **Watch prefill.** Long contexts pay a multi-minute prefill at ~205 t/s.
  Prefer many small calls (refine rounds) over one giant 128k-context call
  unless the task truly needs `batch`.
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
- **Heavy-tier scheduling** — the heavy models are now documented (`heavy` and
  `judge` aliases), but the queue that evicts a warm model only off-hours still
  needs to be wired.
- **LLM-level dashboards** — export `runs.db` aggregates to Prometheus
  (textfile collector) once there are enough runs to trend.
