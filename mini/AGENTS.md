# AGENTS.md — mini

Guidance for AI coding agents working on the **mini** provisioning subtree.

## What this subtree is

Bare-metal Infrastructure-as-Code that provisions `mini` — a single headless
Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395 "Strix Halo", gfx1151, 128 GB unified
RAM) running **Ubuntu Server 26.04 LTS (kernel 7.0)** — from a wiped disk to a fully
configured LLM inference node. Ansible is the configuration engine; cloud-init
NoCloud handles the unattended OS install.

There is **no application code** here — it is Ansible roles, Jinja2 templates,
cloud-init YAML, and a Makefile. Treat it as ops/config, not a software project.

For the companion workstation see [`../ser5/`](../ser5/README.md).

## Layout

All paths below are relative to `mini/` (this directory).

```
Makefile                 entry points (provision, lint, syntax-check, vault-*)
autoinstall/             cloud-init NoCloud (unattended 26.04 install)
ansible/
  site.yml               master playbook; defines role order
  inventory.ini          single host: mini
  requirements.yml       Galaxy collection deps
  group_vars/
    all.yml              node identity, pinned versions, Ollama env, gpu_backend, flags
    vault.yml            ansible-vault encrypted secrets
  roles/
    base/                kernel cmdline (GRUB), firmware pin, packages, UFW, data disk
    amdgpu_rocm/         ROCm 7.14 userspace (TheRock tarball) + Vulkan primary/fallback wiring
    ollama/              Ollama server + systemd override
    tailscale/           tailnet join
    containers/          rootless Podman; user lingering; subuid/subgid; quadlet support
    llamacpp/            llama-server Podman quadlets, one per instance (enable_llamacpp)
    cloudflared/         Cloudflare Tunnel (Podman quadlet; remote-managed token)
```

Role execution order is fixed in `ansible/site.yml`:
`base → amdgpu_rocm → ollama → tailscale → containers → llamacpp → cloudflared`.
Dependencies flow top-to-bottom (e.g. `ollama` assumes GPU userspace is already
installed; `cloudflared` assumes `containers` has already set up quadlet support).
Open WebUI lives on ser5 now (`roles/openwebui` there) — mini stays inference-only.

## Commands

Run from `mini/` on the Linux/WSL control node. Ansible **cannot execute on the
Windows side** (it errors out even if the package is present) — `yamllint` is the only
check that runs there.

The Makefile auto-detects a gitignored `.vault_pass` file: if present it is used for all
vault operations (no prompts, and `syntax-check`/`lint` can load the encrypted vault);
if absent, `make provision` falls back to prompting (`--ask-vault-pass`).

| Command | Purpose |
|---------|---------|
| `make install-deps` | Install Galaxy collections (run once) |
| `make syntax-check` | Validate playbook syntax (uses `.vault_pass` if present) |
| `make lint` | `ansible-lint` + `yamllint` |
| `make provision` | Converge mini (idempotent; `.vault_pass` or prompts) |
| `make ping` | SSH connectivity check |
| `make vault-edit` / `make vault-encrypt` | Manage `ansible/group_vars/vault.yml` |

Always run `make lint` and `make syntax-check` after editing roles or vars.

## Conventions

- **Idempotency is mandatory.** Every change must be safe to re-run via
  `make provision`. Use `creates:`, `state: present`, idempotency guards, or proper
  modules instead of unguarded `command`/`shell`.
- **Prefer fully-qualified modules** (`ansible.builtin.apt`, `community.general.ufw`).
  New collections must be added to `ansible/requirements.yml`.
- **No hardcoded secrets.** Secrets live only in `ansible/group_vars/vault.yml` (vaulted).
  Plaintext vault content must never be committed. Unset values use `PLACEHOLDER_*`
  tokens — keep the Placeholder Table in `README.md` in sync when adding one.
- **Pinned versions live in `ansible/group_vars/all.yml`** (and `amdgpu_rocm/defaults/main.yml`
  for the ROCm/amdgpu-install URL). When bumping a version, update the Pinned Versions
  table in `README.md` too.
- **Tunables are variables, not literals.** Ollama env, GPU backend, disk paths, etc.
  are vars consumed by templates (e.g. `ansible/roles/ollama/templates/override.conf.j2`).
- **Node identity is single-source.** The OS user/home/hostname come from `node_user`,
  `node_home`, `node_hostname` in `ansible/group_vars/all.yml` (and `ansible_user` derives
  from `node_user`). Never hardcode the username, `/home/<user>`, or the hostname in a
  role — reference these vars. `autoinstall/user-data` is static cloud-init and must be
  kept in sync by hand.
- **Comment the non-obvious.** This box runs a consumer APU on a stack that only
  recently gained official support; explain
  *why* for any gfx1151/ROCm/kernel workaround, not just *what*.
- Match the existing YAML style: two-space indent, `name:` on every task,
  box-drawing comment dividers, handlers in `handlers/main.yml`.

## Model policy (bandwidth-bound, not task-depth-bound)

Serving is `llama-server`, not Ollama. Two instances run as Podman quadlets, defined
in `roles/llamacpp` (`llamacpp_instances`), named by ROLE rather than by model so the
model can be swapped without renaming the unit:

| Unit | Port | Model | Slots | Ctx/slot | MTP |
|---|---|---|---|---|---|
| `llama-quality` | 8090 | `qwen3.6-35b-a3b-mtp-q4_K_M` | 4 | 131072 | on |
| `llama-throughput` | 8091 | `gpt-oss-20b-MXFP4` | 8 | 131072 | off |

Both are MoE with ~3B active parameters, because Strix Halo decode speed tracks active
parameters read per token, not headline size. A dense model of the same size is a
mistake here: the old dense `qwen3.6:27b-mtp-q4_K_M` measured ~11-15 t/s.

Context is per SLOT and partitioned statically at startup (`-c` total / `-np` slots),
so a single chat can never exceed 131072 no matter how idle the box is. Raising
ctx/slot means lowering slot count or raising total, and total has a hard ceiling —
see the context warning below.

Ollama is installed but `stopped`/`disabled` (`ollama_service_*` in group_vars). It is
for trying a model by hand, not for serving; it cannot hold weights at the same time as
llama-server in 122 GiB. Its restart handler is gated on `ollama_service_state` so that
editing its env vars does not start it — handlers flush after the task that stopped it.

Roles live in opencode prompts, not baked Modelfiles.

## llama.cpp serving path (`roles/llamacpp`, `enable_llamacpp`)

Podman **quadlets**, one per entry in `llamacpp_instances`, enabled at boot. This
is what Open WebUI on ser5 points at. Full rationale and measurements are in
`roles/llamacpp/defaults/main.yml`.

```
systemctl --user start|stop|status llama-servers.target   # all instances
systemctl --user restart llama-quality                     # just one
```

**Names are roles, aliases are models** — `name: quality` becomes
`llama-quality.service`/`llama-quality`, while `alias:` is what clients send as
`model`. Swapping the model behind a role changes the alias and leaves the unit
name alone; renaming an *alias* breaks clients that hardcoded it (Open WebUI
addresses these by alias). Adding a third instance is an entry with a free port —
it joins the target automatically.

Quadlets replaced `distrobox enter` units in 2026-08. distrobox was never
required: the image is an ordinary OCI image (empty entrypoint,
`Cmd=/bin/bash`) and only the *image* is special — it carries the gfx1151-patched
Mesa/RADV userspace and a llama.cpp built against it. What distrobox cost was
control: it is a `podman exec` client, so the unit's cgroup held only that client
while llama-server lived in the container's cgroup, and `systemctl --user stop`
returned success while the server kept running and holding its port. A quadlet
runs llama-server as the container's main process, so stop/restart just work —
verified: stopping the target leaves 0 survivors and releases both ports.
Throughput was equal or slightly better after the move. Re-measured 2026-08-04 on
ROCm 7.14: quality 86-95 tok/s at c=1 and 142 tok/s aggregate at c=4; throughput
76 tok/s at c=1 and 202 tok/s aggregate at c=8.

The things that bite:

- **MTP is a per-workload trade, not a free win.** `--spec-type draft-mtp` with
  `--spec-draft-n-max 1` buys interactive latency and costs aggregate throughput.
  Acceptance is much better than earlier notes claimed — measured 2026-08-04 from
  `llamacpp:tokens_predicted_total / llamacpp:n_decode_total` at concurrency 1
  (max 2.00): code 1.94, reasoning-with-thinking 1.94, prose 1.70, i.e. 85-97% of
  drafts accepted, against 0.99 on the non-MTP instance. The old "50-58%" figure
  is superseded. **The ~29% aggregate cost has NOT been re-measured since the
  ROCm 7.14 rebuild** — treat it as unverified before using it to justify
  `llamacpp_mtp: false`. Measure the ratio at concurrency 1 only: continuous
  batching decodes every active slot in one step, which inflates it regardless of MTP.
  The ceiling is structural — verifying n+1 tokens routes to up to `8*(n+1)`
  experts instead of 8, so longer drafts cost what they save.
  Confirm it is live by grepping the journal for `MTP draft context`; without the
  flag the loader silently logs `unused tensor blk.40.nextn.* -- ignoring`.
- **`seccomp=unconfined` is not optional.** The ROCm/Vulkan userspace makes
  ioctls the default Podman profile blocks, and the symptom is not a permission
  error — the GPU simply fails to enumerate and llama.cpp falls back to CPU.
  This is why the role sets it on every instance.
- **Ollama and llama-server cannot both be up.** llama-server holds ~22 GiB
  resident. `ollama.service` stays installed for model management but is not
  started alongside it.
- **`-c` is TOTAL context, divided by `-np`** — llama.cpp statically partitions
  the KV cache (no PagedAttention-style pooling). Per-slot is `-c / -np`, and a
  single request over that share fails outright with `request (N tokens) exceeds
  the available context size (M tokens)` even on an idle server. Size it for the
  worst-case single request: `ctx 32768` with `parallel 8` gives each caller only
  4096 tokens, which an ordinary Open WebUI chat overruns — that exact mistake
  produced `request (5945 tokens) exceeds the available context size (4096
  tokens)` in Open WebUI. Both instances now run 131072/slot.
- **DO NOT raise context blind — it can hang the box.** `-c 2097152` (262144/slot)
  does not fail cleanly: it wedges the amdgpu DRM suballocator with the process
  stuck in uninterruptible sleep (`state Ds`, `wchan drm_suballoc_new`),
  immune to SIGKILL, holding ~63 GiB and the GPU device. Recovery required a
  reboot, and the shutdown itself hung for ~7 minutes because systemd cannot kill
  a `D`-state task. The real ceiling is what the DRM suballocator will hand out
  without hanging, which is well below what fits in 122 GiB. Step up one
  increment at a time and confirm each load before committing it.

Models arrive through the `hf` CLI (pipx-installed by this role, because Ubuntu
26.04 is PEP 668 externally-managed). `HF_HOME` is `llamacpp_hf_home` under
`llamacpp_models_dir`, exported to host shells via `/etc/profile.d/huggingface.sh`
so a download lands beside the models. `vault_hf_token` is optional and only
matters for gated repos; unset writes no token file.

- **`70-kfd.rules` is load-bearing.** Rootless Podman maps uids into a user
  namespace, and a host GID that is not mapped there cannot satisfy the kernel
  check on `/dev/kfd` — so the node user's `render`/`video` membership does not
  reach inside the container. The rule (0666 on `kfd` and `renderD*`) is
  upstream's documented fix. Removing it does not produce a permission error:
  the GPU silently fails to enumerate and llama.cpp falls back to CPU, which
  reads as ~2 tok/s instead of ~87.
- **`Network=host` means these are real ports.** A quadlet binds mini's actual
  `:8090`/`:8091`, so UFW (tailnet-only) is the single control point. Never
  reuse 11434 — that is Ollama's.
- Always pass `-fa 1` and `--no-mmap` on Strix Halo; without them llama.cpp
  crashes or crawls. Both are in `llamacpp_base_args`.

## gfx1151 / Strix Halo gotchas (high blast radius — be careful)

- **gfx1151 is officially supported as of ROCm 7.14.0** (2026-07-15), along with
  Ubuntu 26.04. `HSA_OVERRIDE_GFX_VERSION` is **no longer needed** — 7.14 reports
  `Name: gfx1151 / AMD RYZEN AI MAX+ 395 w/ Radeon 8060S` natively, verified on
  the box. The var survives only for the pre-7.14 apt rollback path. Three older
  notes here claimed the opposite; they were true in 2026-07 and are not now.
- ROCm is **7.14.0, installed from TheRock per-architecture tarball**, not apt.
  This matters: `repo.radeon.com/rocm/apt` is frozen at 7.2.4, so checking it and
  concluding "we are current" is a trap — 7.14 ships through a different channel.
  The gfx1151 build is 8.3 GiB installed against 22 GiB for the all-arch apt
  stack. Still userspace-only; the in-tree `amdgpu` drives the GPU (DKMS does not
  build on kernel 7.0 and is unnecessary).
- **Never switch to ROCm 7 NIGHTLIES** — they cap memory at 64 GB. 7.14.0 is a
  production release and is not affected; do not confuse the two.
- **Nothing on the serving path consumes host ROCm.** Ollama runs Vulkan
  (`OLLAMA_VULKAN=1`) and `roles/llamacpp` carries Mesa/RADV inside its image.
  ROCm is here as the `gpu_backend: rocm` fallback and for host tooling
  (`rocminfo`, `rocm-smi`). Judge changes to it on that basis, not on serving
  throughput.
- **Never install `linux-firmware-20251125`** — it breaks ROCm. The `base` role pins it
  out via `/etc/apt/preferences.d/no-bad-firmware`.
- Kernel cmdline (`amd_iommu=off amdgpu.gttsize=131072 ttm.pages_limit=33554432`) is
  managed in `base` via `/etc/default/grub`. A change notifies the `update-grub` handler
  and sets the `reboot_needed` fact; the final play in `ansible/site.yml` then reboots
  the host (when `auto_reboot: true`, the default).
- Strix Halo upstream recommends `amd_iommu=off` (benchmarked 5-12% faster than
  any IOMMU-enabled mode). mini deliberately runs `iommu=pt amd_iommu=on` instead,
  because the amdxdna NPU driver needs SVA and fails to bind without it. That is a
  known, accepted trade-off — do not "fix" it to match upstream's docs without
  deciding to give up the NPU first.
- BIOS settings (latest BIOS, UMA 512MB, IOMMU off, power mode) are a **manual one-time
  prerequisite** — out of Ansible's scope. See `README.md`.
- `gpu_backend: vulkan` in `ansible/group_vars/all.yml` is the committed primary; ROCm
  is the one-line fallback. Keep both code paths working.

## Verifying changes

You cannot run the playbook against hardware from here. Before considering a change
done: run `make lint` + `make syntax-check`, confirm idempotency by reasoning through
re-runs, and keep `README.md` (Pinned Versions, Placeholder Table, verify block)
consistent with the code.

## Do not

- Commit plaintext secrets or decrypted `ansible/group_vars/vault.yml`.
- Add unguarded `command`/`shell` tasks that break idempotency.
- Introduce DKMS, ROCm nightlies, or the bad firmware build.
- Create docs/markdown files unless explicitly asked.
- Touch anything in `../ser5/` — that is a separate machine with its own conventions.
