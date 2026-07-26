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

## Phase 1 — Measurement foundation (do first; everything else is judged by it)

Quality claims and consulting proposals both need numbers. The harness exists
(loopkit); the suites don't.

| Deliverable | Detail |
|---|---|
| Three SMB-shaped eval suites | `extraction.jsonl` (structured data from messy docs), `citation-qa.jsonl` (answer + cite the right source snippet), `structured-output.jsonl` (JSON to schema, validated by scorer) |
| One coding suite | Small, real tasks from your own repos — scored by tests, not string match |
| Baseline matrix | Every suite × {single, refine, best_of_n} × {general, coder} recorded in runs.db |
| Bake-off ritual | Documented quarterly procedure: candidate model → same suites → compare `loopkit stats` → adopt/reject. First job: confirm the depth swap locally and settle the driver-slot challenge. The two-model policy stays; only the *occupants* change |

**Acceptance:** `loopkit stats` shows a full baseline matrix; a one-page
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
| Client repoint | `LOOPKIT_BASE_URL` + opencode `baseURL` are the only integration points |

**Acceptance:** ≥2× decode throughput measured on the eval suites, or the role
stays disabled and the deferral is re-documented with data.
**Effort:** 1–2 sessions. Risk: gfx1151 quirks — mitigated by the flip-back var.

## Phase 3 — Cortex: the second brain

### Design principles

- **Plain files are the brain; software is replaceable.** Markdown vault +
  SQLite indexes. Everything restic-snapshotted and git-versionable. No lock-in,
  no server whose death loses knowledge.
- **The index is disposable; the vault is sacred.** Rebuildable from the vault
  at any time (`cortex reindex`).
- **LLMs draft, files decide.** Every automated write lands as a reviewable
  note or diff — the brain never silently rewrites itself (the ACE lesson:
  incremental deltas, no wholesale rewrites).
- **ser5 thinks slowly and always; mini thinks hard on demand.** Embeddings and
  indexing on ser5's CPU (always-on, ~10W); synthesis on mini's warm pair.

### Architecture

```
capture → inbox → ingest → index → retrieve → synthesize → maintain
                                                              │
                    (nightly/weekly loops feed lessons back) ─┘

/data/brain/                    ser5, Syncthing-synced to devices, restic-backed
  inbox/            raw drops: quick notes, URLs, files (phone → Syncthing)
  notes/            atomic markdown notes (wiki-links, tags, YAML frontmatter)
  sources/          archived originals: web clips (readable text), PDFs, transcripts
  journal/          daily notes + auto-generated daily digest
  mocs/             maps of content (auto-refreshed, human-owned)
  playbooks/        ACE playbooks per domain (consulting, infra, writing…)
  clients/<name>/   ISOLATED per-client subvaults (separate index, see Phase 4)
  .cortex/          SQLite: chunks + FTS5 + sqlite-vec embeddings (disposable)
```

| Component | Choice | Why this is the best fit for this hardware |
|---|---|---|
| Vault format | Markdown + wiki-links (Obsidian-compatible) | Editable everywhere, graph UI for free, survives every tool change |
| Sync | Syncthing (ser5 ↔ workstation ↔ phone) | Offline-first, no cloud, phone capture into `inbox/` |
| Embeddings | Small embedder (e.g. `qwen3-embedding:0.6b`-class) via Ollama **on ser5, CPU** | Preserves mini's two-model policy; personal-scale corpus (100k chunks ≈ 300 MB of vectors) is trivial for the 5800H |
| Index | SQLite: FTS5 (BM25) + sqlite-vec, hybrid via reciprocal-rank fusion | Zero-dependency (matches loopkit philosophy), single backed-up file, plenty below ~1M chunks |
| Rerank (optional) | `general` on mini, listwise, only for high-stakes queries | LLM rerank when it matters, cheap hybrid the rest of the time |
| Synthesis | `general`/`coder` on mini, answers **must cite note paths** | Citations make answers auditable — and demoable |
| Package | `packages/cortex` (stdlib-only where possible), deployed by an Ansible `cortex` role on ser5 | Same pattern as loopkit/agentlab: rsync-push, venv, systemd timers |

### Interfaces

- `cortex add` / `cortex clip <url>` / `cortex ask "…"` / `cortex reindex` (CLI)
- Obsidian over Syncthing for reading/editing/graph (no server needed)
- Phone: Syncthing folder → drop a note or file into `inbox/`, ingested within minutes
- Phase 4 adds the chat UI on top of the same retrieval API

### The maintenance loops (what makes it a *brain*, not a search box)

All run as systemd timers on ser5, built on loopkit primitives, all writing
reviewable output:

| Loop | Cadence | What it does |
|---|---|---|
| Inbox triage | nightly | Convert raw drops into atomic notes: title, tags, wiki-links to related notes (via retrieval), source archived. Drafts land with a `#needs-review` tag |
| Daily digest | nightly | One journal note: what entered the brain, what it connects to |
| MOC refresh | weekly | Re-derive each map-of-content from link/tag clusters; propose additions as a diff |
| Contradiction sweep | weekly | Retrieval-pair new notes against old; flag conflicts ("note A says X, note B says Y") for human resolution |
| Resurfacing | weekly | Spaced-repetition queue: important-but-decaying notes surface in the digest |
| Playbook reflection | after engagements | Feed outcomes through loopkit's ACE reflector into domain playbooks |

**Acceptance:** vault syncs to phone + workstation; `cortex ask` answers with
correct citations against a 500+ note corpus; `citation-qa.jsonl` (Phase 1)
scores the pipeline; nightly triage runs unattended for two weeks without
corrupting anything.
**Effort:** the largest phase — 4–6 sessions. Sequence: index+ask first (a
useful search-and-answer tool in one session), loops incrementally after.

## Phase 4 — Consulting productization

Make the sovereignty pitch enforceable and the lab demoable.

| Deliverable | Detail |
|---|---|
| Demo surface | Open WebUI on ser5 (Podman quadlet, `enable_webui`), behind Cloudflare Access, wired to mini + a cortex demo vault of sample SMB docs. This is what a non-technical buyer sees |
| Tier enforcement made real | Hermes/gateway config that hard-pins Tier L (mini), requires explicit flags for Tier G (Copilot Pro+), Tier X (SuperGrok/Grok Build), and Tier Z (OpenCode Zen), and *logs every escalation attempt*. Until wired, soften the claims in `business-layer.md` to match reality |
| Per-client isolation pattern | `clients/<name>/` subvault + separate cortex index + separate `LOOPKIT_DATA` + documented teardown (what gets deleted at engagement end, including journald and snapshots policy) |
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
