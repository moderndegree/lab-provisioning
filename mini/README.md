# lab-provisioning

Bare-metal IaC for the Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395 "Strix Halo",
128 GB unified RAM, two NVMe slots). Provisions `mini` — a headless Ubuntu Server
26.04 LTS node (kernel 7.0) — from a freshly wiped disk to a fully configured state.

**Primary GPU backend: RADV/Vulkan** (committed primary; AMD ROCm fallback via a single var flip).
**Remote access: Tailscale** (private tailnet; primary) **+ optional Cloudflare Tunnel**
(public, browser-reachable, gated by Cloudflare Access). See [Remote access](#remote-access).

---

## Wipe → Reflash → Pull → Provision Loop

```
1.  Wipe the OS disk and boot the autoinstall USB
2.  Autoinstall runs unattended → SSH-reachable box (≈ 10 min)
3.  From your laptop:  git clone https://github.com/moderndegree/lab-provisioning  &&  cd lab-provisioning/mini
4.  make init                    (once — copies *.example files to local gitignored counterparts)
5.  Edit .bootstrap.env          (credentials, OS disk, mini's IP — copy from ser5/.bootstrap.env and change HOSTNAME + HOST_IP)
6.  make render                  (generates autoinstall/user-data and ansible/inventory.ini)
7.  make install-deps            (once — installs Ansible collections)
8.  make vault-encrypt           (once — encrypt vault.yml after filling in secrets)
9.  make provision               (idempotent — safe to re-run at any time)
```

After the first provision: `make provision` is the single converge command. Re-run it
after any change to roles or vars to bring mini to the desired state.

> **Heads-up:** the **first** `make provision` ends by rebooting mini once, to apply the
> gfx1151 kernel cmdline (`iommu=pt`/`amd_iommu=on`/`gttsize`/`ttm.pages_limit`). Ansible waits for the
> box to come back, so the run still completes cleanly. Steady-state re-runs do **not**
> reboot (the cmdline is unchanged). Set `auto_reboot: false` in `group_vars/all.yml` to
> handle the reboot yourself.

---

## Repo Layout

```
lab-provisioning/
  README.md                         this file
  Makefile                          make provision | ping | syntax-check | lint
  .gitignore
  autoinstall/
    user-data                       cloud-init NoCloud — unattended OS install
    meta-data                       empty (required by NoCloud source)
  ansible/
    requirements.yml                Ansible collection dependencies
    inventory.ini                   single host: mini
    site.yml                        master playbook
    group_vars/
      all.example.yml               placeholder template → copied to all.yml by make init
      all.yml                       (gitignored) node identity, pinned versions, Ollama env, gpu_backend, flags
      vault.yml.example             placeholder secrets → copied to vault.yml by make init
      vault.yml                     (gitignored) ansible-vault encrypted secrets (NEVER commit plaintext)
    roles/
      base/                         kernel cmdline (GRUB), packages, UFW, data disk
      amdgpu_rocm/                  ROCm stack + Vulkan primary/fallback wiring
      ollama/                       Ollama LLM server + systemd override
      harness/                      (empty — opencode and Hermes both on ser5/workstation)
      tailscale/                    tailnet join
      containers/                   rootless Podman; user lingering; quadlet support
      cloudflared/                  Cloudflare Tunnel (Podman quadlet; token-gated start)
      openwebui/                    Open WebUI chat UI (Podman quadlet; enable_openwebui: true)
```

---

## Prerequisites (on your provisioning machine)

- Ansible ≥ 2.15 — `pip install ansible`
- ansible-lint — `pip install ansible-lint`
- yamllint — `pip install yamllint`
- cloud-init CLI (for validating user-data) — `pip install cloud-init` or Ubuntu package

Install Ansible collections once:
```bash
make install-deps
```

---

## BIOS — one-time prerequisites (survives an SSD wipe)

These are set in firmware, not by Ansible, so they persist across reflashes. Do them
once before the first autoinstall:

1. **Update to the latest BIOS first** (fixes idle noise + DPC latency).
2. Integrated Graphics / **UMA Frame Buffer Size → 512MB**.
3. **Enable IOMMU** (or leave at BIOS default — do **not** disable it). Ansible passes
   `iommu=pt amd_iommu=on` on the kernel cmdline; IOMMU pass-through mode is required
   for SVA (Shared Virtual Addressing), which the amdxdna NPU driver uses to avoid
   "SVA bind device failed" on kernel 7.0. Disabling IOMMU in the BIOS would break this.
4. **Power mode: Performance** (130W sustained / 160W peak) or **Rack** (140W sustained).
   A headless box should take the throughput and ignore fan noise.

---

## Autoinstall USB

1. Download Ubuntu Server 26.04 LTS ISO
2. Flash to USB (Balena Etcher, `dd`, or Ventoy)
3. Run `make init` then fill in `.bootstrap.env`, then `make render` to generate `autoinstall/user-data`
4. Replace the ISO's `NoCloud` data source with the files in `autoinstall/`:
   - `autoinstall/user-data` → the rendered autoinstall config
   - `autoinstall/meta-data` → empty file (required)

   The standard approach is to serve them via a second partition or a local HTTP
   server at boot. See: https://ubuntu.com/server/docs/install/autoinstall

---

## Node identity (user / hostname)

The OS user and hostname are parameterized in `ansible/group_vars/all.yml` as a single
source of truth — `node_user` (default `YOUR_USERNAME`), `node_home`, and `node_hostname`
(defaults to the inventory host name). Everything Ansible touches (service accounts,
file ownership, home paths, the SSH login via `ansible_user`, the Tailscale hostname)
derives from these.

cloud-init is static and **cannot** read these vars, so if you change `node_user` /
`node_hostname` you must also edit the matching `username:` / `hostname:` in
`autoinstall/user-data` to keep them aligned.

---

## Secrets Setup (vault.yml)

```bash
# 1. Edit ansible/group_vars/vault.yml and replace all PLACEHOLDER_* values
#    with your real secrets (see Placeholder Table below).

# 2. Encrypt the file with a strong passphrase:
make vault-encrypt

# 3. Commit the resulting ciphertext ($ANSIBLE_VAULT;1.1;AES256...).
#    The plaintext file must NEVER be committed.

# Future edits:
make vault-edit
```

---

## GPU Backend

Default: **RADV/Vulkan** (`gpu_backend: "vulkan"` in `ansible/group_vars/all.yml`).

ROCm remains installed as the fallback path. If Vulkan regresses or a workload needs
ROCm, change one line and re-run `make provision` - no other changes needed:

```yaml
# ansible/group_vars/all.yml
gpu_backend: "rocm"   # was: vulkan
```

Mesa Vulkan drivers are always installed regardless of backend.

**Note on gfx1151 (Strix Halo):** The Radeon 8060S (gfx1151) is NOT on AMD's official
ROCm support matrix. The `HSA_OVERRIDE_GFX_VERSION=11.5.1` environment variable (set in
both `/etc/profile.d/rocm.sh` and the Ollama systemd override) makes the HSA runtime
recognise the GPU as the nearest supported RDNA3 target. ROCm is installed **userspace
only** (`amdgpu-install --no-dkms`); the in-tree `amdgpu` driver that ships with kernel
7.0 drives the GPU. This is a community-proven workaround - re-run the verify block
(below) after any kernel or ROCm bump.

---

## Verify after boot

After the first provision (which reboots mini automatically — see above) and after any
kernel or ROCm bump, confirm the GPU stack on mini before trusting it:

```bash
cat /proc/cmdline                 # confirm iommu=pt amd_iommu=on + GTT/ttm flags applied
                                  # (these only appear after the reboot)
rocminfo | grep -i gfx1151        # ROCm sees the GPU
dmesg | grep -i gtt               # GTT sizing
id ollama                         # service account has render + video groups
journalctl -u ollama | grep -i rocm
#   expect: library=ROCm compute=gfx1151 ... total ~111 GiB available ~110 GiB
```

Measured anchor on this node: `qwen3.6:35b-a3b-mtp-q4_K_M` (22 GB, MoE,
3B active) decodes at **70-80 tok/s** and prefills at **~205 tok/s**, implying
~185 GB/s effective bandwidth (~86% of the ~215 GB/s ceiling). Dense models do
not get the same win: the old `qwen3.6:27b-mtp-q4_K_M` reads 17 GB/token and
lands around **11-15 tok/s**. On mini, decode tracks active parameters read per
token, not headline parameter count.

---

## Model policy - two warm base models

mini keeps exactly **two base models resident**, at the global
**131072-token window**, with no baked system prompts (agent roles live in the
workstation opencode config and loopkit prompts - see `ollama_base_models` in
`ansible/group_vars/all.yml`):

| Slot | Model | Shape | Used for |
|------|-------|-------|----------|
| DEPTH | `qwen3-coder-next:latest` | 80B-A3B MoE hybrid Gated-DeltaNet (3B active) | complex coding + deep reasoning |
| DRIVER | `qwen3.6:35b-a3b-mtp-q4_K_M` | 35B-A3B MoE (3B active) | general tasks, orchestration |

This is a bandwidth box, not a compute box: Strix Halo has ~215 GB/s theoretical
memory bandwidth, and decode speed tracks the **active parameters read per token**.
The measured driver anchor hits 70-80 tok/s at ~185 GB/s effective bandwidth; the
old dense `qwen3.6:27b-mtp-q4_K_M` reads 17 GB/token and only manages ~11-15
tok/s while scoring ~11-15 SWE-bench Verified points below `qwen3-coder-next`.
MoE wins enormously here; a dense model in a warm slot is a mistake. The dense 27B
stays on disk as the one-line rollback in `ollama_base_models`.

`OLLAMA_MAX_LOADED_MODELS=2` + `OLLAMA_KEEP_ALIVE=-1` pin the pair; running any
third model evicts one of them (deliberate - the pair is the fleet). The 131072
window is global because `/v1` cannot set `num_ctx` per request and a Modelfile
`num_ctx` below native is ignored. KV cost is context × parallel, so 128k at
`OLLAMA_NUM_PARALLEL=2` is the same ~256k-token KV budget as 64k at 4-way:
weights (51 + 22 GB) plus ~22 GB of q8_0 KV budget ~95 GB of the ~110 GB GPU
pool. The trade is concurrency - a third simultaneous request queues. 256k does
not fit this pair and would cost ~21 minutes of prefill before first token
anyway; 128k costs ~10 minutes worst case, which is a ceiling, not the norm.

### Heavy tier

Pulled and kept on disk, **never resident** - loading either one evicts a warm
model, so schedule them off-hours (for example an `agentlab-run@heavy-*` job on
ser5):

| Model | Shape | Use |
|-------|-------|-----|
| `gpt-oss:120b` | 117B-A5.1B MoE (5.1B active), 65 GB | best general reasoning that fits; hard-problem tier |
| `nemotron-cascade-2:latest` | 30B-A3B Mamba2-Transformer MoE (~3.6B active), 24 GB | math/algorithm escalation and independent judge |

Reasoning is a per-request concern: `reasoning_effort: "none"` on `/v1` disables
thinking (verified on Ollama 0.31.2; `/no_think` and a `think:false` body field
are both ignored on `/v1` - native `/api/chat` honours `think:false`).

---

## Watch-outs

- **No ROCm nightlies (7.9–7.12).** They cap memory allocation at 64 GB — useless on
  this 128 GB box. Stay on the pinned 7.2.x production stream.
- **Leave RAM headroom.** The warm pair is 51 + 22 = 73 GB of weights plus ~22 GB
  of q8_0 KV at `OLLAMA_CONTEXT_LENGTH=131072` × `OLLAMA_NUM_PARALLEL=2`, or ~95 GB
  of the ~110 GB GPU pool. Context and parallel multiply into the KV budget - raise
  one only by lowering the other. Loading a third/non-resident model evicts a warm
  model and can still pressure unified memory.
- **Re-verify after any bump.** Re-run the verify block above after any kernel or ROCm
  upgrade before trusting the node.
- **gfx1151 is community-supported only.** Pin versions; nothing here is on AMD's
  official ROCm support matrix.
- **Services are tailnet-only.** UFW denies all inbound except SSH (port 22) and the
  entire `tailscale0` interface. Ollama binds `0.0.0.0` (`ollama_host`) so it is reachable
  to tailnet peers but **not** the LAN. Set `ollama_host: 127.0.0.1` to keep it loopback-only.

---

## Make Targets

| Target | Description |
|--------|-------------|
| `make provision` | Run the full playbook (converge) |
| `make ping` | Test SSH connectivity to mini |
| `make syntax-check` | Validate playbook syntax without connecting |
| `make lint` | Run ansible-lint + yamllint |
| `make vault-edit` | Decrypt, edit, and re-encrypt vault.yml |
| `make vault-encrypt` | Encrypt plaintext vault.yml for the first time |
| `make install-deps` | Install required Ansible collections (run once) |

---

## Remote access

Two complementary paths reach mini from anywhere — **nothing inbound is ever opened
on your router** in either case.

| | Tailscale (primary) | Cloudflare Tunnel (secondary) |
|---|---|---|
| Reaches | Your own enrolled devices | Anyone — via a browser/URL |
| Public exposure | **None** (private tailnet) | Public hostname **gated by Cloudflare Access** |
| Client needed | Tailscale app | Just a browser |
| Use it for | Day-to-day secure access | Browser access without a VPN client; sharing |

UFW denies all inbound except SSH (22) and the entire `tailscale0` interface, so the
services themselves stay off the LAN/WAN; the tunnel reaches them via loopback.

### Tailscale (already on by default)

Provisioned by the `tailscale` role. Put a reusable/ephemeral auth key in
`vault_tailscale_authkey`, provision, then install Tailscale on your laptop/phone and
log in to the same tailnet. Reach mini at `mini` (MagicDNS) or its `100.x.y.z` address.

### Cloudflare Tunnel (`enable_cloudflared: true`)

> **Security:** a tunnel hostname is **public by default**, and the Ollama API has
> **no auth of its own** — anyone with the URL could use your GPU. You
> **must** put a Cloudflare Access policy in front of every hostname. With Access, only
> people you allow (e.g. your Google account) ever reach mini.

This repo uses a **remote-managed (token) tunnel**: the only secret in the repo is the
connector token (vaulted). Your domain, the hostname→service routes, and the Access
policies all live in the Cloudflare Zero Trust dashboard — **never in this repo.**

One-time setup (you already have Cloudflare DNS, which is the prerequisite):

1. **Create the tunnel** — Zero Trust dashboard → *Networks → Tunnels → Create a tunnel
   → Cloudflared*. Copy the connector **token**.
2. **Store the token** — `make vault-edit`, set `vault_cloudflared_token` to that token.
   (`enable_cloudflared` is already `true` in `group_vars/all.yml`. Until the token is
   real, the tunnel stays stopped and provisioning still succeeds.)
3. **Add public hostnames** to the tunnel (in the dashboard), each routing to a local
   service on mini — for example:
   - `ollama.<your-domain>` → `http://localhost:11434`
   - `chat.<your-domain>` → `http://localhost:8080` (Open WebUI, see below)

   Cloudflare creates the DNS records for you (since your DNS is on Cloudflare). The
   `cloudflared` quadlet runs with `Network=host` specifically so these `localhost`
   routes reach mini's real ports (Ollama and Open WebUI are rootless Podman/native
   services on the host network, not siblings in cloudflared's own container network).
4. **Gate each hostname with Access** — Zero Trust → *Access → Applications → Add a
   self-hosted app* for each hostname, with a policy that allows only you (e.g. your
   email / Google login). Do this **before** relying on the tunnel.
5. `make provision` — the tunnel comes up and stays running (`Restart=always`).

Tuning (optional, in `group_vars/all.yml`): pin `cloudflared_image` to a release
tag/digest for reproducibility and set `cloudflared_autoupdate: false`.

### Open WebUI (`enable_openwebui: true`)

Browser chat UI for mini's own Ollama, deployed as a rootless Podman quadlet
(`roles/openwebui`, requires `roles/containers` for lingering/quadlet support).

- **Reaches Ollama via `host.containers.internal`**, Podman's built-in host-gateway
  alias — not over Tailscale. This is the answer to "won't Tailscale's DNS get in the
  way here?": since Ollama is local to mini, Open WebUI never needs to resolve a
  tailnet hostname at all, so Tailscale's MagicDNS resolver (which takes over
  `/etc/resolv.conf` on the host once `tailscale up` runs) is simply never in the
  path. This matters only if you later add a service that needs to reach a *different*
  tailnet host (e.g. ser5) from inside a container — see the `DNSSearch=` trick in
  ser5's `roles/observability/templates/obs.network.j2` if that comes up.
- **Reachable at `http://mini:8080`** over the tailnet (same UFW model as Ollama:
  bound `0.0.0.0`, but UFW denies everything inbound except SSH and the entire
  `tailscale0` interface) and via the Cloudflare Tunnel once you add a hostname
  routing to `http://localhost:8080`.
- **First-run setup:** the first account you create in the UI becomes the admin.
  `openwebui_enable_signup: true` (default) leaves signup open for that first run;
  flip it to `false` in `group_vars/all.yml` and re-run `make provision` afterward
  to stop accepting new self-service signups — important if you expose it via the
  tunnel, since a public hostname with open signup is an unauthenticated door onto
  your GPU regardless of what Cloudflare Access does for the route itself.
- Session cookies are signed with a `WEBUI_SECRET_KEY` generated once on first
  provision and persisted at `{{ data_mount }}/services/openwebui/webui.env`
  (`0600`, owned by `node_user`) — re-provisioning does not invalidate sessions.

---

## Bootstrap Setup

All operator-specific values live in `.bootstrap.env` (gitignored). Run once:

```bash
make init        # copies .bootstrap.env.example  → .bootstrap.env
                 #        inventory.example.ini    → ansible/inventory.ini
                 #        all.example.yml          → ansible/group_vars/all.yml
                 #        vault.yml.example        → ansible/group_vars/vault.yml
```

Then edit `.bootstrap.env`. If you already have `ser5/.bootstrap.env` with your
credentials, copy it and change just two lines:

```bash
cp ../ser5/.bootstrap.env .bootstrap.env
# then edit:
#   HOSTNAME=mini
#   HOST_IP=<mini’s IP after autoinstall>
```

Finally render the generated files:

```bash
make render
```

---

## Placeholder Table

After `make render`, only the vault secrets still need manual entry:

| # | Placeholder | Where | How to get it |
|---|-------------|-------|---------------|
| 1 | `PLACEHOLDER_TAILSCALE_AUTH_KEY` | `vault.yml` → `vault_tailscale_authkey` | https://login.tailscale.com/admin/settings/keys |
| 2 | `PLACEHOLDER_CLOUDFLARED_TOKEN` | `vault.yml` → `vault_cloudflared_token` | Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared (tunnel stays stopped until set) |

---

## TODOs (must resolve before first provision)

**TODO 1 — ROCm package set for gfx1151 — RESOLVED**
The `amdgpu_rocm` role installs ROCm 7.2.4 production userspace with
`amdgpu-install -y --usecase=rocm --no-dkms`. DKMS is skipped because it fails to
build on kernel 7.0 and the APU uses the in-tree `amdgpu` driver. Do **not** use
ROCm 7 nightlies — they cap memory allocation at 64 GB (useless on 128 GB).
NOTE: AMD has not published a 26.04 ROCm repo; the role intentionally pulls the
`noble` (24.04) `amdgpu-install` deb — the userspace it installs runs on 26.04.
Change the codename in `roles/amdgpu_rocm/defaults/main.yml` if AMD ships a 26.04 repo.

**TODO 2 — Minimum kernel version for gfx1151 — RESOLVED**
The gfx1151 stability floor is kernel **>= 6.18.4**. Ubuntu Server 26.04 LTS ships
kernel 7.0, which clears it with no HWE or mainline-PPA juggling — so the `base` role
installs no extra kernel package. The `linux-firmware-20251125` ROCm breakage was fixed
upstream in 2026; no apt pin is required.

---

## Pinned Versions (as of 2026-06-19)

| Component | Version | Source |
|-----------|---------|--------|
| Ubuntu Server | 26.04 LTS | ubuntu.com |
| Kernel (baseline) | 7.0 (GA) | 26.04 default — clears gfx1151 >= 6.18.4 floor |
| amdgpu-install | 7.2.4.70204-1 (ROCm 7.2.4) | repo.radeon.com (noble) |
| Ollama | latest (official installer) | ollama.com |
| Tailscale | latest (official installer) | tailscale.com |

