# lab-provisioning

Monorepo for the AI home lab: bare-metal IaC for two machines plus the
software that runs on them. Each machine has its own subdirectory with a full
autoinstall + Ansible provisioning stack; lab software lives under
[`packages/`](packages/).

---

## Machines

| Directory | Machine | Role |
|-----------|---------|------|
| [`mini/`](mini/README.md) | Minisforum MS-S1 Max (Ryzen AI Max+ 395, 128 GB) | Headless LLM inference node; Vulkan/ROCm + Ollama (`qwen3-coder-next:latest` + `qwen3.6:35b-a3b-mtp-q4_K_M`, 131072 window) |
| [`ser5/`](ser5/README.md) | Beelink SER5 (Ryzen 7 5800H, 64 GB) | Always-on driver: Hermes, Open WebUI, observability, backups |

## Software

| Directory | What |
|-----------|------|
| [`docs/operating-manual.md`](docs/operating-manual.md) | One-page entry point: which harness when, which model where, and how to drive it from the phone |
| [`docs/provisioning-checklist.md`](docs/provisioning-checklist.md) | One-time manual steps for the llama.cpp serving change — Ansible cannot remove what it no longer manages |
| [`packages/inference-bench/`](packages/inference-bench/README.md) | Benchmarks for mini's llama.cpp serving path — the numbers behind the `llamacpp` role's sizing |
| [`workstation/`](workstation/README.md) | opencode config for the 9-agent coding team (copied/symlinked onto the dev box) |
| [`docs/brain.md`](docs/brain.md) | Second brain vault on ser5 (`/data/brain`); UI/MCP in sibling **ai-workstation** |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased plan: quality measurement, throughput, the cortex second brain, consulting productization |
| [`docs/todo.md`](docs/todo.md) | Open punch list from the last critical review — operational follow-ups, not roadmap work |

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
│   ├── operating-manual.md  one-page harness/model/phone operating guide
│   └── roadmap.md           phased lab/productization plan
├── packages/
│   └── inference-bench/     serving benchmarks for the llamacpp role
│       ├── pyproject.toml
│       ├── suites/          starter eval suites (JSONL)
│       └── tests/
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
        └── roles/{base,desktop,storage,devtools,virtualization,containers,tailscale,hermes,brain,openwebui,observability,backups}/
    └── autoinstall/
        ├── user-data.example  ${PLACEHOLDER} template
        └── meta-data
```
