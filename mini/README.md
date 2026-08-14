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
      amdgpu_rocm/                  ROCm 7.14 userspace (TheRock tarball) + Vulkan wiring
      llama_benchy/                 llama-benchy + the agentic benchmark suite runner
      llamacpp/                     llama-server Podman quadlets + GPU/model prerequisites
      ollama/                       Ollama LLM server + systemd override
      tailscale/                    tailnet join
      containers/                   rootless Podman; user lingering; subuid/subgid; quadlet
      cloudflared/                  Cloudflare Tunnel (Podman quadlet; token-gated start)
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

**Note on gfx1151 (Strix Halo):** as of **ROCm 7.14.0 (2026-07-15)** the Radeon 8060S
(gfx1151) is an **officially supported target**, along with Ubuntu 26.04. `rocminfo`
names it natively — `HSA_OVERRIDE_GFX_VERSION` is no longer required and survives only
for the pre-7.14 apt rollback path.

ROCm is installed from AMD's **per-architecture TheRock tarball**, not apt. This matters:
`repo.radeon.com/rocm/apt` is frozen at 7.2.4, so checking it and concluding "we are
current" is a trap — 7.14 ships through a different channel. The gfx1151 build is 8.3 GiB
installed against 22 GiB for the all-arch apt stack. Still userspace-only; the in-tree
`amdgpu` driver from kernel 7.0 drives the GPU.

Nothing on the serving path actually uses host ROCm — Ollama runs Vulkan and the
llama.cpp quadlets carry Mesa/RADV in their image. It exists as the `gpu_backend: rocm`
fallback and for host tooling (`rocminfo`, `rocm-smi`). Re-run the verify block after
any kernel or ROCm bump.

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
journalctl --user -u llama-quality | grep -iE 'Vulkan|ROCm|gfx1151'
#   expect the RADV device and ~110 GiB available
```

Measured anchor on this node (2026-08-04, ROCm 7.14, llama-server):
`qwen3.6-35b-a3b-mtp-q4_K_M` (22 GB, MoE, 3B active) decodes at **86-95 tok/s**
single-stream and prefills at **~205 tok/s**, implying ~185 GB/s effective
bandwidth (~86% of the ~215 GB/s ceiling). Dense models do not get the same win:
the old `qwen3.6:27b-mtp-q4_K_M` reads 17 GB/token and lands around
**11-15 tok/s**. On mini, decode tracks active parameters read per token, not
headline parameter count. (The older 70-80 tok/s figure in this file was the
Ollama serving path, which is no longer used.)

---

## Model policy - two llama-server instances

Serving is `llama-server`, not Ollama. Two instances run as Podman quadlets from
`llamacpp_instances` in `roles/llamacpp`, named by ROLE so the model can be
swapped without renaming the unit. No baked system prompts — agent roles live in
the workstation opencode config.

| Unit | Port | Model | Shape | Slots | Ctx/slot | MTP | Measured |
|---|---|---|---|---|---|---|---|
| `llama-quality` | 8090 | `qwen3.6-35b-a3b-mtp-q4_K_M` | 35B-A3B MoE (3B active), 22 GB | 4 | **262144** | n-max 3 | 106 tok/s c=1; 109 agg c=4 |
| `llama-deep` | 8091 | `qwen3.8-27b-q4_K_M` | 27B DENSE hybrid, 19 GB (+3.2 GB MTP head) | 1 | **262144** | n-max 5 | ~25 tok/s sustained |

`llama-quality` is the driver — every agent role runs there except `deep`.
`llama-deep` is called deliberately, never in a loop; it is ~4x slower.
`llama-throughput` (nemotron) was retired 2026-08-14 as dominated — quality-with-MTP
beat it on aggregate (106.4 vs 92.1) *and* single-stream (90.8 vs 70.8).

This is a bandwidth box, not a compute box: Strix Halo has ~215 GB/s theoretical
memory bandwidth, and decode speed tracks the **active parameters read per token**.
The quality anchor hits 106 tok/s at ~185 GB/s effective bandwidth; a dense 27B
reads ~18 GB/token and decodes at **11.4 tok/s raw** — measured on Qwen3.8-27B
2026-08-14, matching the old dense `qwen3.6:27b-mtp-q4_K_M` at ~11-15 tok/s.

MoE wins enormously here, and dense is still the wrong DEFAULT. The deep endpoint
is the deliberate exception: MTP at n-max 5 recovers **2.79x** (11.4 -> 31.8 tok/s
short-prompt), which is the only reason a dense model is serviceable on this box.
That multiplier is not free — it is why `mtp_draft_max` must be measured per model
rather than copied, and why deleting the separate MTP head gguf silently drops the
endpoint back to 11 tok/s.

Re-confirmed 2026-08-12 against **Muse Glimmer 30B** (Meta Superintelligence Lab,
dense 29.6B + 1.8B vision encoder, purpose-built for local agentic work). It is a
genuinely strong model — Meta's card beats Qwen3.6-27B on MCP Atlas, DeepSearch
QA, SWE-Bench Pro and AIME 2026 — and it is still the wrong shape for this box:

| solo decode | qwen3.6-35b-a3b (MoE, MTP) | Muse Glimmer (dense) |
|---|---|---|
| tok/s | **91.3** | 12.6 |
| + its own speculative drafter | — | 27 (DFlash, 2.1x) |
| aggregate @ n=2 | **106.5** | 24.2 |

Predicted 8-12 tok/s before measuring, from bandwidth math and from scaling
Meta's own published RTX-5090 / M4-Max / M5-Max figures; measured 12.6. The
prediction method works, so trust it when triaging a candidate — a dense model
here is arithmetic, not opinion. Even with Meta's DFlash drafter working well
(~50-60% acceptance, a real 2.1x) it lands 3.4x behind the MoE anchor.

Note `--spec-draft-n-max` default 3 is optimal: raising it to 8 halved acceptance
(50% -> 24%) and cut throughput to 14 tok/s. Longer drafts are not better drafts.

**Context is per SLOT and partitioned statically at startup** (`-c` total divided
by `-np` slots), so a single chat can never exceed its slot's window no matter how
idle the box is — **262144 on quality** (the model's full native window; the GGUF
reports `qwen35moe.context_length = 262144`) and 131072 on throughput — and there
is no per-request `num_ctx`. Raising ctx/slot means lowering
slot count or raising total — and total has a hard ceiling: `-c 2097152` hung the
amdgpu DRM allocator unkillably and needed a reboot. `llamacpp_ctx_warn` guards
against it. Prefill slows as context deepens — measured 1025 t/s at depth 0,
652 at 65536, and 486 t/s on a real 137622-token request — so a packed 262144
prompt costs roughly 9-12 minutes before the first token,
so the window is capacity, not a target.

MTP acceptance on `:8090`, measured at concurrency 1 from
`llamacpp:tokens_predicted_total / llamacpp:n_decode_total` (max 2.00): code 1.94,
reasoning 1.94, prose 1.70 — 85-97% of drafts accepted, against 0.99 on `:8091`.
Measure it at concurrency 1 only; continuous batching decodes every active slot in
one step and inflates the ratio regardless of MTP.

Ollama is installed but **stopped and disabled**. It is for pulling and trying a
model by hand, sized `context 32768 / parallel 1 / max_loaded 1 / keep_alive 5m`.
It cannot serve alongside llama-server — the two cannot both hold weights in
122 GiB — so stop an instance first. Thinking is disabled per request with
`chat_template_kwargs: {enable_thinking: false}`; budget tokens for it when on,
since reasoning can consume the whole `max_tokens` and return empty content.

---

## llama.cpp serving path (`enable_llamacpp: true`)

Podman quadlets, one per entry in `llamacpp_instances`, generated into
`llama-<name>.service`. Two by default:

| Instance | Port | Model | Sizing |
|----------|------|-------|--------|
| `llama-quality` | 8090 | `qwen3.6-35b-a3b-mtp` (q4_K_M) | 262144/slot x 4, MTP n-max 3 |
| `llama-deep` | 8091 | `qwen3.8-27b` (q4_K_M) | 262144/slot x 1, MTP n-max 5, f16 KV, separate `-md` head |

```bash
systemctl --user start|stop|status llama-servers.target   # all instances
systemctl --user restart llama-quality                     # just one
journalctl --user -u llama-quality -f
```

`name` is the **role** and becomes the unit and container; `alias` is the **model
identity** clients send as `model`. Swapping a model changes the alias and leaves
the unit name alone. Adding a third instance is one entry with a free port — it
joins the target automatically; removing an entry stops and prunes it.

The image is `kyuz0/amd-strix-halo-toolboxes:vulkan-radv`, which carries a
gfx1151-patched Mesa/RADV userspace and a llama.cpp built against it — the whole
reason for using it rather than a stock llama.cpp container. It is an ordinary
OCI image, so it runs directly under Podman; distrobox is not involved.

For interactive work against the same stack:

```bash
podman run --rm -it --device /dev/dri --device /dev/kfd \
  --security-opt seccomp=unconfined -v /data/models:/data/models:ro \
  docker.io/kyuz0/amd-strix-halo-toolboxes:vulkan-radv /bin/bash
```

GGUFs are staged into `/data/models` with the `hf` CLI (installed by this role);
the role asserts every configured model exists and fails the play if one is
missing. `roles/toolboxes` used to own the udev rule, model dir and HF CLI and
was removed 2026-08 — it wrapped this same image in distrobox, which bought
nothing a quadlet does not do better and cost the ability to stop the server.

## Watch-outs

- **No ROCm nightlies (7.9–7.12).** They cap memory allocation at 64 GB — useless on
  this 128 GB box. The pinned stream is **7.14.0 from the TheRock tarball**, which is
  where gfx1151 became officially supported (2026-07-15). Do not "fix" this back to
  7.2.x: that is only what `repo.radeon.com/rocm/apt` is frozen at, not the current
  release.
- **Leave RAM headroom.** The two llama-server instances hold 22 + 12 = 34 GB of
  weights plus their KV, and measured ~72 GB resident of the ~110 GB GPU pool.
  KV cost is ctx × slots, so raising one means lowering the other. Do not start
  Ollama while they run — it cannot hold weights alongside them in 122 GiB.
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

   Cloudflare creates the DNS records for you (since your DNS is on Cloudflare). The
   `cloudflared` quadlet runs with `Network=host` specifically so this `localhost`
   route reaches mini's real port (Ollama is a native service on the host network,
   not a sibling in cloudflared's own container network).
4. **Gate each hostname with Access** — Zero Trust → *Access → Applications → Add a
   self-hosted app* for each hostname, with a policy that allows only you (e.g. your
   email / Google login). Do this **before** relying on the tunnel.
5. `make provision` — the tunnel comes up and stays running (`Restart=always`).

Tuning (optional, in `group_vars/all.yml`): pin `cloudflared_image` to a release
tag/digest for reproducibility and set `cloudflared_autoupdate: false`.

Open WebUI (browser chat UI) lives on **ser5** now, not mini — see
[`ser5/README.md`](../ser5/README.md#open-webui-enable_openwebui-true). mini's
hard rule is inference only: no loops, no experiments, nothing else.

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
| 3 | `PLACEHOLDER_HF_TOKEN` | `vault.yml` → `vault_hf_token` | https://huggingface.co/settings/tokens — **optional**, only for gated repos (`meta-llama/*`, `google/gemma-*`). No token file is written while unset |

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

