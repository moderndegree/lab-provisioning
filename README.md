# lab-provisioning

Monorepo for the AI home lab: bare-metal IaC for two machines plus the
software that runs on them. Each machine has its own subdirectory with a full
autoinstall + Ansible provisioning stack; lab software lives under
[`packages/`](packages/).

---

## Machines

| Directory | Machine | Role |
|-----------|---------|------|
| [`mini/`](mini/README.md) | Minisforum MS-S1 Max (Ryzen AI Max+ 395, 128 GB) | Headless LLM inference node; ROCm/Vulkan + Ollama (two warm base models, 256k windows) |
| [`ser5/`](ser5/README.md) | Beelink SER5 (Ryzen 7 5800H, 64 GB) | Always-on driver: agentlab (loopkit), Hermes, observability, backups |

## Software

| Directory | What |
|-----------|------|
| [`packages/loopkit/`](packages/loopkit/README.md) | AI loop strategies (refine, best-of-N, ACE playbooks, STaR bootstrapping, evals) against mini's models — deployed to ser5 by the `agentlab` role |
| [`workstation/`](workstation/README.md) | opencode config for the 9-agent coding team (copied/symlinked onto the dev box) |
| [`docs/ai-loops.md`](docs/ai-loops.md) | Architecture + runbook for running AI loop experiments on the lab |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased plan: quality measurement, throughput, the cortex second brain, consulting productization |

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
│   └── ai-loops.md          AI loop architecture + experiment runbook
├── packages/
│   └── loopkit/             loop strategies library + CLI (pure-stdlib Python)
│       ├── pyproject.toml
│       ├── src/loopkit/     client, loops, playbook, evals, star, storage, cli
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
│       └── roles/{base,amdgpu_rocm,ollama,harness,tailscale,cloudflared}/
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
        └── roles/{base,desktop,storage,workstation,virtualization,containers,tailscale,observability,backups}/
    └── autoinstall/
        ├── user-data.example  ${PLACEHOLDER} template
        └── meta-data
```
