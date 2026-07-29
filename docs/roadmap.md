# Lab roadmap — quality, consulting, and the second brain

Two goals drive everything here:

1. **Best quality possible from self-hosted models** — measured, not vibes.
2. **A foundation for AI consulting to small businesses** — where the lab is the
   dev environment, demo floor, and reference implementation, but never client
   production hosting.

The connecting thesis: the **second brain (cortex)** is both the personal
knowledge system and the productized reference implementation of what you sell
— "private AI over your business's documents and knowledge, on hardware you
control." Build it once for yourself; demo and re-deploy it for clients.

Division of labor stays fixed: **mini** is a frozen inference appliance
(`qwen3-coder-next:latest` + `qwen3.6:35b-a3b-mtp-q4_K_M`, 128k global context);
**ser5** runs everything else — ingest, embeddings, indexes, loops, demos,
backups. See [`operating-manual.md`](operating-manual.md) for the live model
inventory and routing tiers.

---

**Status check-in (2026-07-28):** a review of intent vs. implementation found
Phase 3/4-shaped work (second-brain wiring, business-layer tiers, Hermes
surfaces) had gotten built ahead of Phase 1's own acceptance criteria, even
though the sequencing below says measurement comes first. Answer: yes, the
two-goal split had drifted toward consulting-foundation work at measured
quality's expense. Decision: **Phase 4 is frozen** (see its section) and no
new subsystem work starts until Phase 1's acceptance is met. Re-ask this
question at each quarterly bake-off; update this note if the answer changes.

## Phase 1 — Measurement foundation (do first; everything else is judged by it)

Quality claims and consulting proposals both need numbers. The harness exists
(quality-loop / qloop); the suites don't.

| Deliverable | Detail |
|---|---|
| Three SMB-shaped eval suites | `extraction.jsonl` (structured data from messy docs), `citation-qa.jsonl` (answer + cite the right source snippet), `structured-output.jsonl` (JSON to schema, validated by scorer) |
| One coding suite | Small, real tasks from your own repos — scored by tests, not string match |
| Baseline matrix | Every suite × {single, refine, best_of_n} × {general, coder} recorded in runs.db |
| Bake-off ritual | Documented quarterly procedure: candidate model → same suites → compare `qloop stats` → adopt/reject. First job: confirm the depth swap locally and settle the driver-slot challenge. The two-model policy stays; only the *occupants* change |

**Acceptance:** `qloop stats` shows a full baseline matrix; a one-page
"current quality" summary can be generated from runs.db.
**Effort:** 1–2 sessions. No new infrastructure.

## Phase 2 — Throughput (the biggest remaining quality lever)

Test-time compute is throughput-bound: refine ×3 rounds or best-of-8 at
~2.5× the tokens/s is a direct quality multiplier at constant wall-clock.
The repo's own measurements put llama.cpp/Vulkan at ~98–103 t/s vs ~40 on the
same 30B-class hardware path. The dense-to-MoE depth swap already captured a
large bandwidth win, so the remaining llama.cpp delta is smaller than the
original estimate implied — still worth measuring, not assuming.

| Deliverable | Detail |
|---|---|
| `llamacpp` role on mini, gated by `enable_llamacpp` | llama-swap + llama-server, Vulkan, same two base models (GGUF), same 128k/q8 KV budget at 2-way parallel, tailnet-only port. Ollama stays installed as the fallback — flip a var to revert |
| Benchmark before/after | Phase 1 suites + tokens/s on both backends; adopt only on a measured win |
| Client repoint | `QUALITY_LOOP_BASE_URL` + opencode `baseURL` are the only integration points |

**Acceptance:** ≥2× decode throughput measured on the eval suites, or the role
stays disabled and the deferral is re-documented with data.
**Effort:** 1–2 sessions. Risk: gfx1151 quirks — mitigated by the flip-back var.

## Phase 3 — Cortex: the second brain

**MVP already split across repos:**

- **lab-provisioning** `enable_brain` seeds `/data/brain` (layout, templates,
  playbook link, restic).
- **ai-workstation** is the Phase 3 *interface*: vault graph UI, capture, and
  cortex MCP (`CORTEX_VAULT_DIR=/data/brain`). Point the app at the provisioned
  vault; do not duplicate the UI here.

Cortex *maintenance timers* (nightly triage, contradiction sweep) below are
still future work on ser5; day-to-day use the vault + AI Workstation now.

### What lives here vs. ai-workstation

lab-provisioning's job stops at the filesystem: the `brain` role creates
`/data/brain`'s folder layout, seeds templates, includes it in restic, and
symlinks `playbooks/` to `agentlab/playbooks` so qloop and the vault share one
copy of ACE playbooks. Retrieval, embeddings, indexing, the graph UI, ingest
loops (triage, digest, contradiction sweeps, resurfacing), and the `cortex`
CLI/MCP are ai-workstation's scope, not this repo's — see that repo's own
README and roadmap for what's built and what's planned there. Two governing
principles carry over regardless of which repo does the writing: the vault is
sacred and the index is disposable (rebuild, never trust, the index), and LLMs
draft while files decide (every automated write lands as a reviewable note or
diff, never a silent rewrite).

**Acceptance:** vault seeded on ser5, backed up by restic, readable from
Obsidian and ai-workstation via `CORTEX_VAULT_DIR=/data/brain`.
**Effort:** done — `enable_brain: true`. Everything past this is ai-workstation's
roadmap, not a lab-provisioning session.

## Phase 4 — Consulting productization

**FROZEN (2026-07-28):** no new tier enforcement, client isolation, or
demo-surface work until there is a concrete prospect or pilot engagement to
build it for. Design docs are fine; new code isn't. Lift this once Phase 1's
acceptance is met *and* a real engagement exists — not just because it's
been a while.

Make the sovereignty pitch enforceable and the lab demoable.

| Deliverable | Detail |
|---|---|
| Demo surface | Open WebUI on ser5 (Podman quadlet, `enable_openwebui`), behind Cloudflare Access, wired to mini + a cortex demo vault of sample SMB docs. This is what a non-technical buyer sees |
| Tier enforcement made real | Hermes/gateway config that hard-pins Tier L (mini), requires explicit flags for Tier G (Copilot Pro+), Tier X (SuperGrok/Grok Build), and Tier Z (OpenCode Zen), and *logs every escalation attempt*. Until wired, soften the claims in `business-layer.md` to match reality |
| Per-client isolation pattern | `clients/<name>/` subvault + separate cortex index + separate `QUALITY_LOOP_DATA` + documented teardown (what gets deleted at engagement end, including journald and snapshots policy) |
| Data hygiene | Off-site encrypted restic target (B2/S3) on a second timer; log-retention policy; verify LUKS on mini's disk — physical theft must not equal client data |
| Engagement kit | Proposal template backed by Phase 1 eval numbers; demo script; the "dev here, production on *your* infra" boundary in writing |

**Acceptance:** a stranger with a browser (and an Access grant) can have a
cited conversation with the demo vault; a mock engagement can be provisioned
and torn down cleanly; every sovereignty claim in `business-layer.md` is either
enforced or removed.
**Effort:** 2–3 sessions plus dashboard/account setup only you can do
(Cloudflare Access policies, B2 bucket).

## Phase 5 — Peak quality and the improvement flywheel

| Deliverable | Detail |
|---|---|
| Off-hours heavy queue | `agentlab-run@heavy-*` jobs allowed to evict a warm model overnight for `gpt-oss:120b` or `nemotron-cascade-2`, restoring the "hard problem, take an hour" tier without breaking daytime memory budget. This phase is queueing/scheduling, not model hunting: both are already pulled and documented on mini. Queue in, results + eval scores out by morning |
| STaR → LoRA bridge | Datasets already accumulate. When a suite shows a persistent gap: rent a GPU-hour, LoRA-tune the current depth model on bootstrapped traces, eval on the same suite, adopt only on a win. Revisit native gfx1151 training each quarter |
| LLM-level observability | Export runs.db aggregates + tokens/s to Prometheus (textfile collector); Grafana panel per suite over time — the "is it getting better?" chart, which is also consulting collateral |

**Effort:** 2–3 sessions, after Phases 1–3 provide the measures.

---

## Sequencing and dependencies

```
Phase 1 (suites) ──► Phase 2 (throughput, judged by suites)
      │
      └────────────► Phase 3 (cortex; citation-qa suite scores it)
                          │
                          └───► Phase 4 (demo + isolation productize cortex)
                                     │
Phase 5 (heavy queue, LoRA, dashboards) ◄─ needs 1's measures, helps 3 & 4
```

Standing rituals: quarterly model bake-off (Phase 1 procedure); re-verify the
GPU stack after any kernel/ROCm bump (mini README); restore-test a restic
snapshot monthly.

## Explicitly out of scope (deliberate)

- **Hosting client production** on this hardware — sell systems that run on
  client infra; the lab is for building, evaling, and demoing.
- **Native fine-tuning on gfx1151** until the stack is trustworthy (rented
  GPU bridges the gap).
- **A third resident model on mini** — the two-model policy holds because mini
  is memory-bandwidth-bound; embeddings live on ser5, and heavy/judge/scout
  models are scheduled evictions, not residents.
- **Kubernetes, vector databases, workflow platforms** — SQLite, systemd, and
  Ansible until something measurably outgrows them.
