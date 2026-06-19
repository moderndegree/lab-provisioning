# lab-provisioning

Bare-metal IaC for two machines — a headless AMD inference box and a companion
Linux workstation. Each machine has its own subdirectory with a full
autoinstall + Ansible provisioning stack.

---

## Machines

| Directory | Machine | Role |
|-----------|---------|------|
| [`mini/`](mini/README.md) | Minisforum MS-S1 Max (Ryzen AI Max+ 395, 128 GB) | Headless LLM inference node; ROCm + Ollama + Hermes |
| [`ser5/`](ser5/README.md) | Beelink SER5 (Ryzen 7 5800H, 64 GB) | Linux workstation + always-on companion + IaC test lab |

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
├── mini/                    MS-S1 Max — headless inference node
│   ├── README.md
│   ├── AGENTS.md
│   ├── Makefile
│   └── ansible/
│       ├── site.yml
│       ├── inventory.ini
│       ├── requirements.yml
│       ├── group_vars/
│       │   ├── all.yml      node identity, pinned versions, Ollama/ROCm config
│       │   └── vault.yml    ansible-vault encrypted secrets
│       └── roles/{base,amdgpu_rocm,ollama,harness,tailscale,cloudflared}/
│   └── autoinstall/
│       ├── user-data        cloud-init NoCloud
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
