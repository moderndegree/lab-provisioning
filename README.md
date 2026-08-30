# lab-provisioning

Monorepo for the AI home lab: bare-metal IaC for two machines plus the
software that runs on them. Each machine has its own subdirectory with a full
autoinstall + Ansible provisioning stack; lab software lives under
[`packages/`](packages/).

---

## Machines

| Directory | Machine | Role |
|-----------|---------|------|
| [`mini/`](mini/README.md) | Minisforum MS-S1 Max (Ryzen AI Max+ 395, 128 GB) | Headless LLM inference node; two `llama-server` instances under Podman quadlets — `:8090` quality (`qwen3.6-35b-a3b-mtp-q4_K_M`, 4 slots, MTP on) and `:8091` deep (`qwen3.8-27b-q4_K_M`, 1 slot). Context per slot is 262144 on both. Vulkan/RADV backend, ROCm 7.14 userspace. Ollama is installed but stopped — started by hand only to try a model. |
| [`ser5/`](ser5/README.md) | Beelink SER5 (Ryzen 7 5800H, 64 GB) | Always-on driver: Hermes, Open WebUI, observability, backups |

## Software

| Directory | What |
|-----------|------|
| [`docs/operating-manual.md`](docs/operating-manual.md) | One-page entry point: which harness when, which model where, and how to drive it from the phone |
| [`packages/inference-bench/`](packages/inference-bench/README.md) | Benchmarks for mini's llama.cpp serving path — the numbers behind the `llamacpp` role's sizing |
| [`packages/voice-gateway/`](packages/voice-gateway/README.md) | The voice loop: VAD → STT → mini → TTS with barge-in. 1305 ms to first audible word (measured); desktop push-to-talk client included |
| [`workstation/`](workstation/README.md) | opencode config for the 10-agent coding team (copied/symlinked onto the dev box) |
| [`docs/brain.md`](docs/brain.md) | Second brain vault on ser5 (`/data/brain`); UI/MCP in sibling **ai-workstation** |
| [`docs/todo.md`](docs/todo.md) | Open punch list — operational follow-ups and the open question of what measures quality |

---

## Quick start

```bash
# Mini — inference box
cd mini && make provision

# Ser5 — workstation (first time: init → fill config → render → provision)
cd ser5 && make init
# … edit .bootstrap.env, ansible/group_vars/all.yml, ansible/group_vars/vault.yml …
cd ser5 && make render && make provision
```

See each machine's `README.md` for the full workflow, placeholder table, and role reference.

---

## Repo layout

```
lab-provisioning/
├── .gitattributes
├── .gitignore
├── .yamllint.yml            shared yamllint config (found by both machine Makefiles)
├── docs/
│   ├── operating-manual.md  routing tiers, models, harnesses, phone workflow
│   ├── brain.md             second-brain vault layout and repo boundary
│   ├── provisioning-checklist.md  one-time manual steps Ansible cannot do
│   └── todo.md              open punch list
├── packages/
│   ├── inference-bench/     serving benchmarks behind the llamacpp sizing
│   │   ├── lbench.py        raw decode throughput at a given concurrency
│   │   ├── agentsim.py      workers + orchestrator, tool calls, multi-turn
│   │   └── fanoutsim.py     small fan-out: N workers then a judge pass
│   └── voice-gateway/       the voice loop (ser5); see roles/voice
│       ├── voice_gateway/   VAD, STT, LLM, sentence-chunked TTS, routing
│       ├── clients/desktop/ Windows push-to-talk + wake-word client
│       └── bench/           voicebench.py — the latency numbers in the README
├── workstation/             opencode runtime config for the dev box
├── mini/                    MS-S1 Max — headless inference node
│   ├── README.md
│   ├── AGENTS.md
│   ├── Makefile
│   └── ansible/
│       ├── site.yml
│       ├── inventory.example.ini
│       ├── requirements.yml
│       ├── group_vars/
│       │   ├── all.example.yml      placeholder template (tracked); all.yml is gitignored
│       │   └── vault.yml.example    placeholder secrets (tracked); vault.yml is gitignored
│       └── roles/{base,amdgpu_rocm,ollama,harness,tailscale,containers,cloudflared}/
│   └── autoinstall/
│       ├── user-data.example    ${PLACEHOLDER} template (tracked); user-data is gitignored
│       └── meta-data
└── ser5/                    Beelink SER5 — workstation + companion
    ├── README.md
    ├── Makefile
    ├── .bootstrap.env.example
    ├── .gitignore           ser5-specific gitignore (real all.yml/vault.yml/user-data)
    └── ansible/
        ├── site.yml
        ├── inventory.example.ini
        ├── requirements.yml
        ├── group_vars/
        │   ├── all.example.yml    placeholder params (tracked)
        │   └── vault.yml.example  placeholder secrets (tracked; encrypt after init)
        └── roles/{base,desktop,storage,devtools,virtualization,containers,tailscale,hermes,brain,openwebui,searxng,voice,observability,backups}/
    └── autoinstall/
        ├── user-data.example  ${PLACEHOLDER} template
        └── meta-data
```
