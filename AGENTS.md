# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this repo is

Bare-metal Infrastructure-as-Code that provisions `mini` — a single headless
Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395 "Strix Halo", gfx1151, 128 GB unified
RAM) running **Ubuntu Server 26.04 LTS (kernel 7.0)** — from a wiped disk to a fully
configured LLM inference node. Ansible is the configuration engine; cloud-init
NoCloud handles the unattended OS install.

There is **no application code** here — it is Ansible roles, Jinja2 templates,
cloud-init YAML, and a Makefile. Treat it as ops/config, not a software project.

## Layout

```
Makefile                 entry points (provision, lint, syntax-check, vault-*)
autoinstall/             cloud-init NoCloud (unattended 26.04 install)
ansible/
  site.yml               master playbook; defines role order
  inventory.ini          single host: mini
  requirements.yml       Galaxy collection deps
  group_vars/
    all.yml              pinned versions, Ollama env, gpu_backend, feature flags
    vault.yml            ansible-vault encrypted secrets
  roles/
    base/                kernel cmdline (GRUB), firmware pin, packages, UFW, data disk
    amdgpu_rocm/         ROCm 7.2.4 userspace (--no-dkms) + Vulkan fallback
    ollama/              Ollama server + systemd override
    harness/             Node.js, opencode-ai, Hermes Agent + gateway service
    tailscale/           tailnet join
    cloudflared/         Cloudflare tunnel stub (off by default)
```

Role execution order is fixed in `site.yml`:
`base → amdgpu_rocm → ollama → harness → tailscale → cloudflared`. Dependencies flow
top-to-bottom (e.g. `ollama` assumes ROCm is already installed).

## Commands

Run from the repo root. The provisioning host runs Ansible (typically WSL/Linux);
these tools are **not** installed on the Windows side.

| Command | Purpose |
|---------|---------|
| `make install-deps` | Install Galaxy collections (run once) |
| `make syntax-check` | Validate playbook syntax — no host connection or vault |
| `make lint` | `ansible-lint` + `yamllint` |
| `make provision` | Converge mini (idempotent; prompts for vault pass) |
| `make ping` | SSH connectivity check |
| `make vault-edit` / `make vault-encrypt` | Manage `group_vars/vault.yml` |

Always run `make lint` and `make syntax-check` after editing roles or vars.

## Conventions

- **Idempotency is mandatory.** Every change must be safe to re-run via
  `make provision`. Use `creates:`, `state: present`, idempotency guards, or proper
  modules instead of unguarded `command`/`shell`.
- **Prefer fully-qualified modules** (`ansible.builtin.apt`, `community.general.ufw`).
  New collections must be added to `ansible/requirements.yml`.
- **No hardcoded secrets.** Secrets live only in `group_vars/vault.yml` (vaulted).
  Plaintext vault content must never be committed. Unset values use `PLACEHOLDER_*`
  tokens — keep the Placeholder Table in `README.md` in sync when adding one.
- **Pinned versions live in `group_vars/all.yml`** (and `amdgpu_rocm/defaults/main.yml`
  for the ROCm/amdgpu-install URL). When bumping a version, update the Pinned Versions
  table in `README.md` too.
- **Tunables are variables, not literals.** Ollama env, GPU backend, disk paths, etc.
  are vars consumed by templates (e.g. `ollama/templates/override.conf.j2`).
- **Comment the non-obvious.** This box runs an officially-unsupported GPU; explain
  *why* for any gfx1151/ROCm/kernel workaround, not just *what*.
- Match the existing YAML style: two-space indent, `name:` on every task,
  box-drawing comment dividers, handlers in `handlers/main.yml`.

## gfx1151 / Strix Halo gotchas (high blast radius — be careful)

- GPU is **gfx1151**, not on AMD's official ROCm matrix. Recognition depends on
  `HSA_OVERRIDE_GFX_VERSION=11.5.1` (set in `/etc/profile.d/rocm.sh` and the Ollama
  systemd override). Do not remove it.
- ROCm is pinned to **7.2.4 production**, installed **userspace-only** with
  `amdgpu-install --no-dkms` (the in-tree `amdgpu` drives the GPU; DKMS fails to build
  on kernel 7.0). **Never switch to ROCm 7 nightlies** — they cap memory at 64 GB.
- AMD has no 26.04 ROCm repo yet, so `amdgpu_rocm/defaults/main.yml` intentionally
  pulls the `noble` (24.04) `amdgpu-install` deb. Change the codename only if AMD ships
  a 26.04 repo.
- **Never install `linux-firmware-20251125`** — it breaks ROCm. The `base` role pins it
  out via `/etc/apt/preferences.d/no-bad-firmware`.
- Kernel cmdline (`amd_iommu=off amdgpu.gttsize=131072 ttm.pages_limit=33554432`) is
  managed in `base` via `/etc/default/grub`. A change notifies the `update-grub` handler
  and sets the `reboot_needed` fact; the final play in `site.yml` then reboots the host
  (when `auto_reboot: true`, the default) so one `make provision` fully applies it.
- BIOS settings (latest BIOS, UMA 512MB, IOMMU off, power mode) are a **manual one-time
  prerequisite** — out of Ansible's scope. See `README.md`.
- `gpu_backend: vulkan` in `group_vars/all.yml` is a one-line fallback if ROCm regresses.
  Keep both code paths working.

## Verifying changes

You cannot run the playbook against hardware from here. Before considering a change
done: run `make lint` + `make syntax-check`, confirm idempotency by reasoning through
re-runs, and keep `README.md` (Pinned Versions, Placeholder Table, verify block)
consistent with the code.

## Do not

- Commit plaintext secrets or decrypted `vault.yml`.
- Add unguarded `command`/`shell` tasks that break idempotency.
- Introduce DKMS, ROCm nightlies, or the bad firmware build.
- Create docs/markdown files unless explicitly asked.
