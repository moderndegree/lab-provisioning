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
    amdgpu_rocm/         ROCm 7.2.4 userspace (--no-dkms) + Vulkan primary/fallback wiring
    ollama/              Ollama server + systemd override
    harness/             (empty — opencode and Hermes both on ser5/workstation)
    tailscale/           tailnet join
    containers/          rootless Podman; user lingering; subuid/subgid; quadlet support
    toolboxes/           Strix Halo AI toolboxes (distrobox; enable_toolboxes)
    llamacpp/            llama-server Podman quadlets, one per instance (enable_llamacpp)
    cloudflared/         Cloudflare Tunnel (Podman quadlet; remote-managed token)
```

Role execution order is fixed in `ansible/site.yml`:
`base → amdgpu_rocm → ollama → tailscale → containers → toolboxes → llamacpp → cloudflared`.
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
| `make toolbox-refresh` | Re-pull Strix Halo toolbox images (opt-in; not part of converge) |

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
- **Comment the non-obvious.** This box runs an officially-unsupported GPU; explain
  *why* for any gfx1151/ROCm/kernel workaround, not just *what*.
- Match the existing YAML style: two-space indent, `name:` on every task,
  box-drawing comment dividers, handlers in `handlers/main.yml`.

## Model policy (bandwidth-bound, not task-depth-bound)

mini keeps exactly two warm base models resident at the global 131072 context window:
`qwen3-coder-next:latest` for DEPTH and `qwen3.6:35b-a3b-mtp-q4_K_M` for DRIVER.
Both are MoE with ~3B active parameters because Strix Halo decode speed tracks active
parameters read per token, not headline size. The measured driver anchor is 70-80 t/s
(~185 GB/s effective); the old dense `qwen3.6:27b-mtp-q4_K_M` is only ~11-15 t/s and
stays on disk as rollback, not as a warm slot. The 131072 window is global: `/v1` cannot
set `num_ctx` per request, and Modelfile downscoping is ignored. It is paired with
`OLLAMA_NUM_PARALLEL=2` because context × parallel is the KV budget - raise one only
by lowering the other.

Heavy models (`gpt-oss:120b`, `nemotron-cascade-2:latest`) are pulled but never resident;
loading one evicts a warm model under `OLLAMA_MAX_LOADED_MODELS=2`, so keep them to
scheduled/off-hours jobs. Roles live in opencode/loopkit prompts, not baked Modelfiles.

## Toolboxes (`roles/toolboxes`, `enable_toolboxes`)

Distrobox wrappers around the pre-built images from
[strix-halo-toolboxes.com](https://strix-halo-toolboxes.com/) — llama.cpp
(Vulkan RADV/AMDVLK, ROCm 6.4.4/7.2.4), ComfyUI, vLLM, QLoRA fine-tuning, and
DwarfStar. They are **on-demand shells, not services**: nothing here is enabled at boot,
nothing is bound to a port by Ansible, and the Ollama service on `:11434` is
untouched. `roles/llamacpp` uses the same *image* but runs it as a Podman quadlet
rather than through distrobox, so the toolbox containers stay disposable — you
can `distrobox rm llama-vulkan-radv` without touching the serving path. Their reason to exist
is that each image carries its own
gfx1151-patched GPU userspace, so backends can be A/B-benchmarked without
touching the one ROCm/Vulkan stack `amdgpu_rocm` installs on the host.

Which images get created is `toolboxes_instances` in
`roles/toolboxes/defaults/main.yml`; the default set is the two llama.cpp
backends worth comparing plus `vllm-therock`, with the rest commented out and
ready to uncomment. Each toolbox gets a host launcher at `~/.local/bin/<name>`
and a shared model directory bind-mounted at the same path inside every
container — hence `vllm-therock` rather than `vllm`, which would shadow the real
binary.

`vllm-therock` is a **bench rig only**, and as of 2026-08-02 it lost the one
argument for its existence. It was kept for continuous batching on the theory
that llama.cpp batches poorly here. Measured on Qwen3.6-35B-A3B, single-stream /
aggregate at concurrency 8:

| runtime | c=1 | c=8 |
|---------|-----|-----|
| vLLM 0.22 toolbox, tuned MoE config | 24.8 | 85.7 |
| vLLM 0.20 via Lemonade (AMD's gfx1151 build) | 25.2 | 93.9 |
| llama.cpp `-np 8` | 75.1 | **168.6** |
| llama.cpp `-np 8` + MTP `n=1` | **86.7** | 120.7 |

llama.cpp wins **both** ends, so `roles/llamacpp` is the serving path and vLLM is
for A/B curiosity. Two independent vLLM builds landing within 2% of each other is
the tell: every vLLM-compatible quant of this model (AWQ, compressed-tensors,
FP8) leaves the GatedDeltaNet projections, `lm_head`, `self_attn` and shared
experts in BF16 — about 3.6 GiB of the ~4.15 GiB read per decoded token. GGUF
q4_K_M quantizes all of it. That is a checkpoint property, not a runtime one, so
no vLLM flag closes it. Do not re-litigate this without new numbers.

vLLM also needs `--enable-auto-tool-choice --tool-call-parser qwen3_xml` before
any client that sends a `tools` array works at all, and the parser must be
`qwen3_xml`/`qwen3_coder` rather than the usually-recommended `hermes` — this
model's template emits the XML form, and a mismatched parser fails silently by
never detecting a tool call. llama-server needs none of that (`--jinja` is on by
default). It serves on `:8000`. Any throughput measured while `ollama.service` is
up is noise: two warm models at 131072 context plus a vLLM KV cache do not fit in
122 GiB.

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
required: the toolbox image is an ordinary OCI image (empty entrypoint,
`Cmd=/bin/bash`) and only the *image* is special — it carries the gfx1151-patched
Mesa/RADV userspace and a llama.cpp built against it. What distrobox cost was
control: it is a `podman exec` client, so the unit's cgroup held only that client
while llama-server lived in the container's cgroup, and `systemctl --user stop`
returned success while the server kept running and holding its port. A quadlet
runs llama-server as the container's main process, so stop/restart just work —
verified: stopping the target leaves 0 survivors and releases both ports.
Throughput was equal or slightly better after the move (87.1 vs 85.9 tok/s at
c=1; ~202 vs 190.7 aggregate at c=8).

The things that bite:

- **MTP is a per-workload trade, not a free win.** `--spec-type draft-mtp` with
  `--spec-draft-n-max 1` buys ~15% interactive latency and costs ~29% aggregate
  throughput. Set `llamacpp_mtp: false` for the qloop/agent workload. Acceptance
  is healthy (50-58%); the ceiling is structural — verifying n+1 tokens routes to
  up to `8*(n+1)` experts instead of 8, so longer drafts cost what they save.
  Confirm it is live by grepping the journal for `MTP draft context`; without the
  flag the loader silently logs `unused tensor blk.40.nextn.* -- ignoring`.
- **`seccomp=unconfined` is not optional.** The ROCm/Vulkan userspace makes
  ioctls the default Podman profile blocks, and the symptom is not a permission
  error — the GPU simply fails to enumerate and llama.cpp falls back to CPU.
  Same reason `roles/toolboxes` sets it.
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

Models arrive through the `hf` CLI (pipx-installed on the host by this role,
because Ubuntu 26.04 is PEP 668 externally-managed). `HF_HOME` is
`toolboxes_hf_home` under the shared model dir and is set **twice on purpose** —
`/etc/profile.d/huggingface.sh` for host shells, `--env` in every flag profile
for the containers. They must agree, or `$HF_HOME/token` is invisible to one
side and gated downloads fail there only. `vault_hf_token` is optional and only
matters for gated repos; unset writes no token file.

**`--gpu-memory-utilization` must be passed explicitly** when serving. vLLM's
0.9 default is a fraction of what ROCm reports as device memory, which here is
`amdgpu.gttsize=131072` (128 GiB) on a 122 GiB machine — the default preallocates
a KV cache larger than RAM. 0.75 is the sane starting point.

Three non-obvious constraints:

- **`70-kfd.rules` is load-bearing.** Rootless Podman runs the toolbox under
  `--userns keep-id`; a host GID that is not mapped into that namespace cannot
  satisfy the kernel check on `/dev/kfd`, so the node user's `render`/`video`
  membership does not reliably reach inside the container. The role's udev rule
  (0666 on `kfd` and `renderD*`) is upstream's documented Ubuntu fix. Removing it
  silently breaks GPU access in every toolbox — the GPU stops appearing in
  `llama-cli --list-devices` — while the host itself keeps working fine.
- **Never use a *named* `--group-add`** in `toolboxes_flags_*`. Under rootless
  Podman the name resolves against the container image's `/etc/group` and the
  gid lands inside the user namespace, so `--group-add render` grants no access
  to `/dev/kfd` — and if the image lacks the group the container refuses to
  start outright. No `--group-add` is needed at all: distrobox already sets
  `--annotation run.oci.keep_original_groups=1` on every rootless create, which
  is what `--group-add keep-groups` compiles down to (crun only; Ubuntu's
  Podman defaults to crun).
- **Toolboxes share the host network namespace** (also ipc and pid — distrobox
  shares all three by default). A `llama-server` or `vllm serve` started inside
  one binds a real mini port — never 11434.

Upstream tells you to always pass `-fa 1` and `--no-mmap` to llama.cpp on Strix
Halo; without them it crashes or crawls. The wrapper script header repeats this.

## gfx1151 / Strix Halo gotchas (high blast radius — be careful)

- GPU is **gfx1151**, not on AMD's official ROCm matrix. Recognition depends on
  `HSA_OVERRIDE_GFX_VERSION=11.5.1` (set in `/etc/profile.d/rocm.sh` and the Ollama
  systemd override). Do not remove it.
- ROCm is pinned to **7.2.4 production**, installed **userspace-only** with
  `amdgpu-install --no-dkms` (the in-tree `amdgpu` drives the GPU; DKMS fails to build
  on kernel 7.0). **Never switch to ROCm 7 nightlies** — they cap memory at 64 GB.
- AMD has no 26.04 ROCm repo yet, so `ansible/roles/amdgpu_rocm/defaults/main.yml`
  intentionally pulls the `noble` (24.04) `amdgpu-install` deb. Change the codename only
  if AMD ships a 26.04 repo.
- **Never install `linux-firmware-20251125`** — it breaks ROCm. The `base` role pins it
  out via `/etc/apt/preferences.d/no-bad-firmware`.
- Kernel cmdline (`amd_iommu=off amdgpu.gttsize=131072 ttm.pages_limit=33554432`) is
  managed in `base` via `/etc/default/grub`. A change notifies the `update-grub` handler
  and sets the `reboot_needed` fact; the final play in `ansible/site.yml` then reboots
  the host (when `auto_reboot: true`, the default).
- The toolboxes upstream recommends `amd_iommu=off` (benchmarked 5-12% faster than
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
